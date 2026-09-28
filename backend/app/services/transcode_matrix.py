from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import tempfile
from threading import Lock
from time import perf_counter

from backend.app.core.config import Settings
from backend.app.schemas.transcoding import (
    TranscodeCapabilitiesRead,
    TranscodeCapabilityMatrixRead,
    TranscodeDeviceMatrixRead,
    TranscodeEncoderCapability,
    TranscodeHardwareDevice,
    TranscodeMatrixBenchmarkLevelRead,
    TranscodeMatrixBenchmarkRead,
    TranscodeMatrixBenchmarkRunRead,
    TranscodeMatrixCellRead,
    TranscodeMatrixTestProgressRead,
)
from backend.app.services.transcoding import (
    _encoder_quality_spec,
    _hardware_backend,
    _hardware_device_arguments,
    _quality_option,
    get_transcode_capabilities,
)
from backend.app.utils.processes import get_hidden_subprocess_kwargs
from backend.app.utils.time import utc_now


MATRIX_LOCK = Lock()
MATRIX_PROGRESS_LOCK = Lock()
MATRIX_PROGRESS = {"running": False, "completed": 0, "total": 0}
MATRIX_DIRECTORY = "transcoding-tests"
MATRIX_RESULT_FILE = "capability-matrix.json"
VIDEO_CODEC_ORDER = ("h264", "hevc", "av1", "vp9", "vp8", "mpeg2video", "mjpeg")
SOFTWARE_ENCODERS = {
    "h264": ("libx264",),
    "hevc": ("libx265",),
    "av1": ("libsvtav1", "libaom-av1"),
    "vp9": ("libvpx-vp9",),
    "vp8": ("libvpx",),
    "mpeg2video": ("mpeg2video",),
    "mjpeg": ("mjpeg",),
}
# The matrix searches for the first measurable performance boundary instead of
# treating an arbitrary probe count as the device capacity.  Twenty is high
# enough to cover common desktop use while keeping a runaway local test bounded.
MAX_PARALLEL_PROBE_JOBS = 20
PARALLEL_PROBE_REPETITIONS = 3
PARALLEL_PERFORMANCE_TOLERANCE = 0.20
PARALLEL_PROBE_WIDTH = 256
PARALLEL_PROBE_HEIGHT = 256
PARALLEL_PROBE_FRAME_RATE = 30
PARALLEL_PROBE_FRAMES = 240
PARALLEL_PROBE_STREAM_LOOPS = 7
MATRIX_CELL_FRAMES = 30
MATRIX_FINGERPRINT_VERSION = 1


class TranscodeMatrixBusyError(RuntimeError):
    pass


def _set_transcode_matrix_progress(*, running: bool, completed: int, total: int) -> None:
    with MATRIX_PROGRESS_LOCK:
        MATRIX_PROGRESS.update(
            running=running,
            completed=max(0, completed),
            total=max(0, total),
        )


def transcode_matrix_test_progress() -> TranscodeMatrixTestProgressRead:
    with MATRIX_PROGRESS_LOCK:
        return TranscodeMatrixTestProgressRead(**MATRIX_PROGRESS)


def _matrix_root(settings: Settings) -> Path:
    root = (Path(settings.config_path) / MATRIX_DIRECTORY).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _result_path(settings: Settings) -> Path:
    return _matrix_root(settings) / MATRIX_RESULT_FILE


def transcode_capability_fingerprint(capabilities: TranscodeCapabilitiesRead) -> str:
    """Return a stable fingerprint for the environment a matrix measures.

    Probe timestamps and transient human-readable errors are deliberately
    excluded.  The fingerprint instead covers the FFmpeg build, platform,
    codec inventory, and physical-device properties that affect the matrix.
    """

    encoder_payload = [
        {
            "name": encoder.name,
            "codec": encoder.codec,
            "hardware": encoder.hardware,
            "available": encoder.available,
            "tested": encoder.tested,
            "device_ids": sorted(encoder.device_ids),
            "options": sorted(encoder.options),
            "quality_mode": encoder.quality_mode,
            "quality_min": encoder.quality_min,
            "quality_max": encoder.quality_max,
            "quality_default": encoder.quality_default,
            "quality_step": encoder.quality_step,
        }
        for encoder in capabilities.encoders
    ]
    encoder_payload.sort(key=lambda item: (item["name"], item["codec"]))
    device_payload = [
        {
            "id": device.id,
            "name": device.name,
            "vendor": device.vendor,
            "backend": device.backend,
            "driver_version": device.driver_version,
            "compute_capability": device.compute_capability,
            "memory_total_bytes": device.memory_total_bytes,
            "render_node": device.render_node,
            "native_device_index": device.native_device_index,
            "device_class": device.device_class,
            "decoder_codecs": sorted(device.decoder_codecs),
            "encoder_names": sorted(device.encoder_names),
            "encoder_codecs": sorted(device.encoder_codecs),
            "supported_pixel_formats": sorted(device.supported_pixel_formats),
            "supported_filters": sorted(device.supported_filters),
            "status": device.status,
        }
        for device in capabilities.devices
    ]
    device_payload.sort(key=lambda item: item["id"])
    payload = {
        "fingerprint_version": MATRIX_FINGERPRINT_VERSION,
        "ffmpeg_available": capabilities.ffmpeg_available,
        "ffmpeg_path": capabilities.ffmpeg_path,
        "version": capabilities.version,
        "ffmpeg_version": capabilities.ffmpeg_version,
        "platform": capabilities.platform,
        "containers": sorted(capabilities.containers),
        "decoder_codecs": sorted(capabilities.decoder_codecs),
        "dolby_vision_passthrough": capabilities.dolby_vision_passthrough,
        "encoders": encoder_payload,
        "devices": device_payload,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_transcode_matrix(settings: Settings) -> TranscodeCapabilityMatrixRead:
    try:
        payload = json.loads(_result_path(settings).read_text(encoding="utf-8"))
        return TranscodeCapabilityMatrixRead.model_validate(payload)
    except FileNotFoundError:
        return TranscodeCapabilityMatrixRead()
    except (OSError, ValueError, TypeError) as exc:
        return TranscodeCapabilityMatrixRead(status="failed", error=f"Stored matrix could not be read: {exc}")


def _store_transcode_matrix(settings: Settings, result: TranscodeCapabilityMatrixRead) -> None:
    target = _result_path(settings)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def _run_command(arguments: list[str], *, timeout: int = 25) -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **get_hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stderr or completed.stdout or "").strip()
    if len(output) > 800:
        output = output[-800:]
    return completed.returncode == 0, output or None


def _timed_run_command(arguments: list[str], *, timeout: int = 25) -> tuple[bool, float, str | None]:
    started = perf_counter()
    succeeded, error = _run_command(arguments, timeout=timeout)
    return succeeded, max(perf_counter() - started, 0.000001), error


def _parallel_capacity_levels_for_limit(maximum_supported: int) -> list[int]:
    """Return the concurrency levels tested for a given stable capacity."""

    levels: list[int] = []
    maximum = 1
    first_failure: int | None = None
    level = 2
    while level <= MAX_PARALLEL_PROBE_JOBS:
        levels.append(level)
        if level > maximum_supported:
            first_failure = level
            break
        maximum = level
        if level == MAX_PARALLEL_PROBE_JOBS:
            break
        level = min(level * 2, MAX_PARALLEL_PROBE_JOBS)

    if first_failure is not None:
        for candidate in range(maximum + 1, first_failure):
            levels.append(candidate)
            if candidate > maximum_supported:
                break
    return levels


def _parallel_capacity_work_units_for_limit(maximum_supported: int) -> int:
    frame_weight = max(PARALLEL_PROBE_FRAMES // MATRIX_CELL_FRAMES, 1)
    probe_jobs = PARALLEL_PROBE_REPETITIONS * (
        1 + sum(_parallel_capacity_levels_for_limit(maximum_supported))
    )
    return probe_jobs * frame_weight


def _parallel_capacity_max_work_units() -> int:
    """Reserve enough work for the most expensive possible boundary search."""

    return max(
        (
            _parallel_capacity_work_units_for_limit(maximum_supported)
            for maximum_supported in range(1, MAX_PARALLEL_PROBE_JOBS + 1)
        ),
        default=PARALLEL_PROBE_REPETITIONS
        * max(PARALLEL_PROBE_FRAMES // MATRIX_CELL_FRAMES, 1),
    )


def _benchmark_work_units(benchmark: TranscodeMatrixBenchmarkRead) -> int:
    frame_weight = max(PARALLEL_PROBE_FRAMES // MATRIX_CELL_FRAMES, 1)
    return sum(
        level.concurrency * len(level.runs) * frame_weight
        for level in benchmark.levels
    )


def _fixture_options(encoder: str) -> list[str]:
    if encoder in {"libx264", "libx265"}:
        return ["-preset", "ultrafast"]
    if encoder == "libsvtav1":
        return ["-preset", "12"]
    if encoder == "libaom-av1":
        return ["-cpu-used", "8"]
    if encoder.startswith("libvpx"):
        return ["-deadline", "realtime", "-cpu-used", "8"]
    return []


def _create_fixture(ffmpeg_path: str, codec: str, directory: Path) -> tuple[Path | None, str | None]:
    errors: list[str] = []
    for encoder in SOFTWARE_ENCODERS[codec]:
        target = directory / f"source-{codec}.mkv"
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={PARALLEL_PROBE_WIDTH}x{PARALLEL_PROBE_HEIGHT}:rate={PARALLEL_PROBE_FRAME_RATE}:duration=1",
            "-an",
            "-c:v",
            encoder,
            *_fixture_options(encoder),
            "-pix_fmt",
            "yuv420p",
            "-frames:v",
            "30",
            str(target),
        ]
        succeeded, error = _run_command(command, timeout=40)
        if succeeded and target.is_file():
            return target, encoder
        if error:
            errors.append(error)
    return None, errors[-1] if errors else "No software fixture encoder is available"


def _software_encoder(capabilities: TranscodeCapabilitiesRead, codec: str) -> str | None:
    preferred = SOFTWARE_ENCODERS[codec]
    available = {
        encoder.name
        for encoder in capabilities.encoders
        if encoder.available and not encoder.hardware
    }
    return next((name for name in preferred if name in available), None)


def _device_hardware_encoder(
    capabilities: TranscodeCapabilitiesRead,
    device: TranscodeHardwareDevice,
    codec: str,
) -> TranscodeEncoderCapability | None:
    candidates = [
        encoder
        for encoder in capabilities.encoders
        if encoder.hardware and encoder.available and encoder.codec == codec
    ]
    for encoder in candidates:
        if device.id in encoder.device_ids or encoder.name in device.encoder_names:
            return encoder
    return None


def _hardware_decode_setup(device: TranscodeHardwareDevice) -> list[str] | None:
    backend = device.backend
    command: list[str] = []
    if backend == "cuda":
        command.extend(_hardware_device_arguments({"cuda"}, None, cuda_device_id=device.id))
        command.extend(["-hwaccel", "cuda", "-hwaccel_device", "cu", "-hwaccel_output_format", "cuda"])
    elif backend in {"qsv", "vaapi"} and device.render_node:
        qsv_direct = backend == "qsv"
        command.extend(_hardware_device_arguments({backend}, device.render_node, qsv_direct=qsv_direct))
        hardware_name = "qs" if qsv_direct else "va"
        command.extend(["-hwaccel", backend, "-hwaccel_device", hardware_name, "-hwaccel_output_format", backend])
    elif backend == "qsv" and device.native_device_index is not None:
        command.extend(
            _hardware_device_arguments(
                {backend},
                None,
                native_device_index=device.native_device_index,
            )
        )
        command.extend(["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"])
    elif backend == "qsv":
        command.extend(["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"])
    elif backend == "amf":
        if device.native_device_index is not None:
            command.extend(
                _hardware_device_arguments(
                    {backend},
                    None,
                    native_device_index=device.native_device_index,
                )
            )
            command.extend(
                [
                    "-filter_hw_device",
                    "amf",
                    "-hwaccel",
                    "d3d11va",
                    "-hwaccel_device",
                    "amf",
                    "-hwaccel_output_format",
                    "d3d11",
                ]
            )
        else:
            command.extend(["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"])
    elif backend == "videotoolbox":
        command.extend(["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"])
    else:
        return None
    return command


def _hardware_decode_probe_command(
    ffmpeg_path: str,
    fixture: Path,
    device: TranscodeHardwareDevice,
    *,
    frames: int = 1,
) -> list[str] | None:
    setup = _hardware_decode_setup(device)
    if setup is None:
        return None
    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", *setup]
    command.extend(
        [
            "-i",
            str(fixture),
            "-map",
            "0:v:0",
            "-an",
            # A hardware-only frame download makes an unsupported decoder
            # fail instead of allowing FFmpeg to continue with software
            # frames after a zero-exit hardware initialisation warning.
            "-vf",
            "hwdownload,format=nv12",
            "-frames:v",
            str(frames),
            "-f",
            "null",
            "-",
        ]
    )
    return command


def _hardware_pair_command(
    ffmpeg_path: str,
    fixture: Path,
    device: TranscodeHardwareDevice,
    encoder: str,
    *,
    frames: int = 30,
    stream_loops: int = 0,
) -> list[str] | None:
    backend = _hardware_backend(encoder)
    if backend != device.backend:
        return None
    setup = _hardware_decode_setup(device)
    if setup is None:
        return None
    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", *setup]
    if stream_loops:
        command.extend(["-stream_loop", str(stream_loops)])
    command.extend(["-i", str(fixture), "-map", "0:v:0", "-an", "-c:v", encoder])
    quality_spec = _encoder_quality_spec(encoder)
    if quality_spec:
        command.extend([f"-{_quality_option(quality_spec[0])}", f"{quality_spec[3]:g}"])
    command.extend(["-frames:v", str(frames), "-f", "null", "-"])
    return command


def _software_pair_command(ffmpeg_path: str, fixture: Path, encoder: str) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(fixture),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        encoder,
        *_fixture_options(encoder),
        "-frames:v",
        "30",
        "-f",
        "null",
        "-",
    ]


def _parallel_capacity(
    command: list[str],
    *,
    on_work_completed: Callable[[int], None] | None = None,
) -> tuple[int, bool, TranscodeMatrixBenchmarkRead]:
    benchmark = TranscodeMatrixBenchmarkRead(
        tolerance_percent=PARALLEL_PERFORMANCE_TOLERANCE * 100,
        test_ceiling=MAX_PARALLEL_PROBE_JOBS,
        repetitions=PARALLEL_PROBE_REPETITIONS,
        width=PARALLEL_PROBE_WIDTH,
        height=PARALLEL_PROBE_HEIGHT,
        frame_rate=PARALLEL_PROBE_FRAME_RATE,
        frames=PARALLEL_PROBE_FRAMES,
        stream_loops=PARALLEL_PROBE_STREAM_LOOPS,
    )

    def run_record(run: int, outcome: tuple[bool, float, str | None]) -> TranscodeMatrixBenchmarkRunRead:
        succeeded, duration, error = outcome
        return TranscodeMatrixBenchmarkRunRead(
            run=run,
            duration_seconds=duration if succeeded else None,
            success=succeeded,
            error=error if not succeeded else None,
        )

    frame_weight = max(PARALLEL_PROBE_FRAMES // MATRIX_CELL_FRAMES, 1)
    baseline_runs = []
    for run in range(1, PARALLEL_PROBE_REPETITIONS + 1):
        baseline_runs.append(run_record(run, _timed_run_command(command, timeout=45)))
        if on_work_completed is not None:
            on_work_completed(frame_weight)
    baseline_durations = [
        run.duration_seconds
        for run in baseline_runs
        if run.success and run.duration_seconds is not None
    ]
    baseline = (
        float(median(baseline_durations))
        if len(baseline_durations) == PARALLEL_PROBE_REPETITIONS
        else None
    )
    slowdown_limit = baseline * (1 + PARALLEL_PERFORMANCE_TOLERANCE) if baseline is not None else None
    benchmark.baseline_median_seconds = baseline
    benchmark.slowdown_limit_seconds = slowdown_limit
    benchmark.levels.append(
        TranscodeMatrixBenchmarkLevelRead(
            concurrency=1,
            runs=baseline_runs,
            median_seconds=baseline,
            slowdown_percent=0.0 if baseline is not None else None,
            passed=baseline is not None,
            error=next((run.error for run in baseline_runs if run.error), None),
        )
    )
    if baseline is None:
        return 1, False, benchmark

    def sample_level(count: int) -> TranscodeMatrixBenchmarkLevelRead:
        runs: list[TranscodeMatrixBenchmarkRunRead] = []
        for run in range(1, PARALLEL_PROBE_REPETITIONS + 1):
            with ThreadPoolExecutor(max_workers=count, thread_name_prefix="transcode-matrix") as executor:
                futures = [
                    executor.submit(_timed_run_command, command, timeout=45)
                    for _index in range(count)
                ]
                outcomes = []
                for future in as_completed(futures):
                    outcomes.append(future.result())
                    if on_work_completed is not None:
                        on_work_completed(frame_weight)
            successful_durations = [outcome[1] for outcome in outcomes if outcome[0]]
            runs.append(
                TranscodeMatrixBenchmarkRunRead(
                    run=run,
                    duration_seconds=(
                        float(median(successful_durations))
                        if successful_durations
                        else None
                    ),
                    success=all(outcome[0] for outcome in outcomes),
                    error=next((outcome[2] for outcome in outcomes if not outcome[0] and outcome[2]), None),
                )
            )
        durations = [
            run.duration_seconds
            for run in runs
            if run.success and run.duration_seconds is not None
        ]
        level_median = float(median(durations)) if len(durations) == PARALLEL_PROBE_REPETITIONS else None
        slowdown_percent = (
            ((level_median / baseline) - 1) * 100
            if level_median is not None
            else None
        )
        return TranscodeMatrixBenchmarkLevelRead(
            concurrency=count,
            runs=runs,
            median_seconds=level_median,
            slowdown_percent=slowdown_percent,
            passed=level_median is not None and level_median <= slowdown_limit,
            error=next((run.error for run in runs if run.error), None),
        )

    def passes_performance_test(count: int) -> bool:
        level_result = sample_level(count)
        benchmark.levels.append(level_result)
        return level_result.passed

    # Probe exponentially first, then scan the small interval before the first
    # failing level. This resolves an exact boundary such as 4 pass / 5 fail,
    # without launching every level up to twenty when the device can handle
    # them all.
    maximum = 1
    first_failure: int | None = None
    level = 2
    while level <= MAX_PARALLEL_PROBE_JOBS:
        if not passes_performance_test(level):
            first_failure = level
            break
        maximum = level
        if level == MAX_PARALLEL_PROBE_JOBS:
            break
        level = min(level * 2, MAX_PARALLEL_PROBE_JOBS)

    if first_failure is None:
        benchmark.levels.sort(key=lambda level_result: level_result.concurrency)
        return maximum, True, benchmark

    for candidate in range(maximum + 1, first_failure):
        if passes_performance_test(candidate):
            maximum = candidate
        else:
            benchmark.levels.sort(key=lambda level_result: level_result.concurrency)
            return maximum, False, benchmark
    benchmark.levels.sort(key=lambda level_result: level_result.concurrency)
    return maximum, False, benchmark


def _codec_axes(capabilities: TranscodeCapabilitiesRead) -> tuple[list[str], list[str]]:
    del capabilities
    codecs = list(VIDEO_CODEC_ORDER)
    return codecs, list(codecs)


def _device_group_key(device: TranscodeHardwareDevice) -> str:
    return f"render:{device.render_node}" if device.render_node else f"device:{device.id}"


def _device_group_name(devices: list[TranscodeHardwareDevice]) -> str:
    first = devices[0]
    if not first.render_node:
        return first.name.removesuffix(" (automatic)")
    name_parts = first.name.split(" · ")
    return " · ".join(name_parts[:-1]) if len(name_parts) > 1 else first.name


def _device_group_class(devices: list[TranscodeHardwareDevice]) -> str:
    classes = {device.device_class for device in devices}
    return next(iter(classes)) if len(classes) == 1 else "unknown"


def _matrix_device_groups(
    capabilities: TranscodeCapabilitiesRead,
) -> dict[str, list[TranscodeHardwareDevice]]:
    device_groups: dict[str, list[TranscodeHardwareDevice]] = {}
    for device in capabilities.devices:
        if device.status == "available":
            device_groups.setdefault(_device_group_key(device), []).append(device)
    return device_groups


def _matrix_test_work_plan(
    capabilities: TranscodeCapabilitiesRead,
) -> tuple[int, dict[tuple[str, str, str], int]]:
    if not capabilities.ffmpeg_available or not capabilities.devices:
        return 1, {}
    decode_codecs, encode_codecs = _codec_axes(capabilities)
    cell_count = len(decode_codecs) * len(encode_codecs)
    device_group_count = len(_matrix_device_groups(capabilities))
    benchmark_work_estimates: dict[tuple[str, str, str], int] = {}
    benchmark_work_reserve = _parallel_capacity_max_work_units()
    for device_key, devices in _matrix_device_groups(capabilities).items():
        for decode_codec in decode_codecs:
            for encode_codec in encode_codecs:
                if any(
                    _device_hardware_encoder(capabilities, device, encode_codec) is not None
                    for device in devices
                ):
                    benchmark_work_estimates[(device_key, decode_codec, encode_codec)] = (
                        benchmark_work_reserve
                    )
    matrix_work = len(decode_codecs) + device_group_count * cell_count
    return max(matrix_work + sum(benchmark_work_estimates.values()), 1), benchmark_work_estimates


def _matrix_test_work_total(capabilities: TranscodeCapabilitiesRead) -> int:
    return _matrix_test_work_plan(capabilities)[0]


def _build_matrices(
    settings: Settings,
    capabilities: TranscodeCapabilitiesRead,
    *,
    on_work_completed: Callable[[int], None] | None = None,
    on_work_total_changed: Callable[[int], None] | None = None,
    benchmark_work_estimates: dict[tuple[str, str, str], int] | None = None,
) -> TranscodeCapabilityMatrixRead:
    tested_at = utc_now()
    decode_codecs, encode_codecs = _codec_axes(capabilities)
    if benchmark_work_estimates is None:
        benchmark_work_estimates = _matrix_test_work_plan(capabilities)[1]
    root = _matrix_root(settings)
    with tempfile.TemporaryDirectory(prefix="run-", dir=root) as temporary_directory:
        temporary_path = Path(temporary_directory)
        fixtures = {}
        for codec in decode_codecs:
            fixtures[codec] = _create_fixture(settings.ffmpeg_path, codec, temporary_path)
            if on_work_completed is not None:
                on_work_completed(1)
        software_results: dict[tuple[str, str], tuple[bool, str | None]] = {}
        hardware_decode_results: dict[tuple[str, str], tuple[bool, str | None]] = {}
        matrices: list[TranscodeDeviceMatrixRead] = []
        device_groups = _matrix_device_groups(capabilities)
        for device_key, devices in device_groups.items():
            cells: list[TranscodeMatrixCellRead] = []
            hardware_commands: dict[tuple[str, str], list[str]] = {}
            for decode_codec in decode_codecs:
                fixture, _fixture_encoder = fixtures[decode_codec]
                for encode_codec in encode_codecs:
                    if fixture is None:
                        cells.append(
                            TranscodeMatrixCellRead(
                                decode_codec=decode_codec,
                                encode_codec=encode_codec,
                                status="not_tested",
                                detail="The synthetic source codec could not be created with this FFmpeg build.",
                            )
                        )
                        if on_work_completed is not None:
                            on_work_completed(1)
                        skipped_benchmark_work = benchmark_work_estimates.get(
                            (device_key, decode_codec, encode_codec), 0
                        )
                        if skipped_benchmark_work and on_work_total_changed is not None:
                            on_work_total_changed(-skipped_benchmark_work)
                        continue
                    hardware_cell: TranscodeMatrixCellRead | None = None
                    for device in devices:
                        hardware_encoder = _device_hardware_encoder(capabilities, device, encode_codec)
                        if hardware_encoder is None:
                            continue
                        decode_key = (device.id, decode_codec)
                        if decode_key not in hardware_decode_results:
                            decode_command = _hardware_decode_probe_command(
                                settings.ffmpeg_path,
                                fixture,
                                device,
                            )
                            hardware_decode_results[decode_key] = (
                                _run_command(decode_command, timeout=40)
                                if decode_command is not None
                                else (False, "No hardware decode probe is available for this backend")
                            )
                        decode_succeeded, _decode_error = hardware_decode_results[decode_key]
                        if not decode_succeeded:
                            continue
                        command = _hardware_pair_command(
                            settings.ffmpeg_path,
                            fixture,
                            device,
                            hardware_encoder.name,
                        )
                        if command is None:
                            continue
                        succeeded, _error = _run_command(command)
                        if succeeded:
                            hardware_commands[(decode_codec, encode_codec)] = command
                            hardware_cell = TranscodeMatrixCellRead(
                                decode_codec=decode_codec,
                                encode_codec=encode_codec,
                                status="hardware",
                                decoder=f"{device.backend}:{decode_codec}",
                                encoder=hardware_encoder.name,
                            )
                            break
                    if hardware_cell is not None:
                        cells.append(hardware_cell)
                        if on_work_completed is not None:
                            on_work_completed(1)
                        continue
                    software_encoder = _software_encoder(capabilities, encode_codec)
                    software_key = (decode_codec, encode_codec)
                    if software_encoder is not None and software_key not in software_results:
                        software_results[software_key] = _run_command(
                            _software_pair_command(settings.ffmpeg_path, fixture, software_encoder),
                            timeout=40,
                        )
                    software_succeeded = software_results.get(software_key, (False, None))[0]
                    cells.append(
                        TranscodeMatrixCellRead(
                            decode_codec=decode_codec,
                            encode_codec=encode_codec,
                            status="software" if software_succeeded else "unsupported",
                            decoder="software:auto" if software_succeeded else None,
                            encoder=software_encoder if software_succeeded else None,
                            detail=(
                                "The complete hardware decode and encode path failed; the software path passed."
                                if software_succeeded
                                else "Neither a complete hardware path nor an available software path passed."
                            ),
                        )
                    )
                    if on_work_completed is not None:
                        on_work_completed(1)
                    skipped_benchmark_work = benchmark_work_estimates.get(
                        (device_key, decode_codec, encode_codec), 0
                    )
                    if skipped_benchmark_work and on_work_total_changed is not None:
                        on_work_total_changed(-skipped_benchmark_work)
            for (decode_codec, encode_codec), base_command in hardware_commands.items():
                representative = list(base_command)
                frames_index = representative.index("-frames:v") + 1
                representative[frames_index] = str(PARALLEL_PROBE_FRAMES)
                input_index = representative.index("-i")
                representative[input_index:input_index] = ["-stream_loop", str(PARALLEL_PROBE_STREAM_LOOPS)]
                benchmark_key = (device_key, decode_codec, encode_codec)
                expected_benchmark_work = benchmark_work_estimates.get(
                    benchmark_key,
                    _parallel_capacity_max_work_units(),
                )
                actual_benchmark_work = 0

                def mark_benchmark_work(work_units: int) -> None:
                    nonlocal actual_benchmark_work
                    actual_benchmark_work += work_units
                    if on_work_completed is not None:
                        on_work_completed(work_units)

                maximum, lower_bound, benchmark = _parallel_capacity(
                    representative,
                    on_work_completed=mark_benchmark_work,
                )
                actual_benchmark_work = _benchmark_work_units(benchmark)
                if on_work_total_changed is not None:
                    on_work_total_changed(actual_benchmark_work - expected_benchmark_work)
                for cell in cells:
                    if (
                        cell.status == "hardware"
                        and cell.decode_codec == decode_codec
                        and cell.encode_codec == encode_codec
                    ):
                        cell.max_parallel_jobs = maximum
                        cell.max_parallel_jobs_is_lower_bound = lower_bound
                        cell.parallel_benchmark = benchmark
            matrices.append(
                TranscodeDeviceMatrixRead(
                    device_id=device_key,
                    device_name=_device_group_name(devices),
                    backend=" + ".join(sorted({device.backend for device in devices})),
                    device_class=_device_group_class(devices),
                    tested_at=tested_at,
                    decode_codecs=decode_codecs,
                    encode_codecs=encode_codecs,
                    cells=cells,
                )
            )
    return TranscodeCapabilityMatrixRead(
        status="completed",
        tested_at=tested_at,
        ffmpeg_version=capabilities.ffmpeg_version or capabilities.version,
        matrices=matrices,
    )


def _matrix_result(
    settings: Settings,
    capabilities: TranscodeCapabilitiesRead,
    capability_fingerprint: str,
    *,
    on_work_completed: Callable[[int], None] | None = None,
    on_work_total_changed: Callable[[int], None] | None = None,
    benchmark_work_estimates: dict[tuple[str, str, str], int] | None = None,
) -> TranscodeCapabilityMatrixRead:
    if not capabilities.ffmpeg_available:
        result = TranscodeCapabilityMatrixRead(
            status="failed",
            tested_at=utc_now(),
            ffmpeg_version=capabilities.ffmpeg_version or capabilities.version,
            error=capabilities.error or "FFmpeg is unavailable",
        )
    elif not capabilities.devices:
        result = TranscodeCapabilityMatrixRead(
            status="completed",
            tested_at=utc_now(),
            ffmpeg_version=capabilities.ffmpeg_version or capabilities.version,
            matrices=[],
        )
    else:
        result = _build_matrices(
            settings,
            capabilities,
            on_work_completed=on_work_completed,
            on_work_total_changed=on_work_total_changed,
            benchmark_work_estimates=benchmark_work_estimates,
        )
    return result.model_copy(update={"capability_fingerprint": capability_fingerprint})


def _run_transcode_matrix_test(
    settings: Settings,
    *,
    only_if_changed: bool,
) -> TranscodeCapabilityMatrixRead:
    if not MATRIX_LOCK.acquire(blocking=False):
        raise TranscodeMatrixBusyError("A transcoding capability test is already running")
    completed_work = 0
    total_work = 1
    _set_transcode_matrix_progress(running=True, completed=completed_work, total=total_work)
    try:
        capabilities = get_transcode_capabilities(settings, refresh=True)
        fingerprint = transcode_capability_fingerprint(capabilities)
        total_work, benchmark_work_estimates = _matrix_test_work_plan(capabilities)
        _set_transcode_matrix_progress(running=True, completed=completed_work, total=total_work)
        if only_if_changed:
            existing = load_transcode_matrix(settings)
            if (
                existing.status == "completed"
                and existing.capability_fingerprint == fingerprint
            ):
                completed_work = total_work
                _set_transcode_matrix_progress(
                    running=False,
                    completed=completed_work,
                    total=total_work,
                )
                return existing

        def mark_work_completed(work_units: int = 1) -> None:
            nonlocal completed_work
            completed_work += max(0, work_units)
            _set_transcode_matrix_progress(
                running=True,
                completed=completed_work,
                total=total_work,
            )

        def adjust_work_total(work_units: int) -> None:
            nonlocal total_work
            total_work = max(completed_work, total_work + work_units)
            _set_transcode_matrix_progress(
                running=True,
                completed=completed_work,
                total=total_work,
            )

        result = _matrix_result(
            settings,
            capabilities,
            fingerprint,
            on_work_completed=mark_work_completed,
            on_work_total_changed=adjust_work_total,
            benchmark_work_estimates=benchmark_work_estimates,
        )
        _store_transcode_matrix(settings, result)
        completed_work = total_work
        _set_transcode_matrix_progress(
            running=False,
            completed=completed_work,
            total=total_work,
        )
        return result
    except Exception:
        _set_transcode_matrix_progress(
            running=False,
            completed=completed_work,
            total=total_work,
        )
        raise
    finally:
        MATRIX_LOCK.release()


def run_transcode_matrix_test(settings: Settings) -> TranscodeCapabilityMatrixRead:
    """Always run a fresh local matrix test after an explicit user request."""

    return _run_transcode_matrix_test(settings, only_if_changed=False)


def run_transcode_matrix_test_if_changed(settings: Settings) -> TranscodeCapabilityMatrixRead:
    """Refresh the local matrix only when the measured environment changed."""

    return _run_transcode_matrix_test(settings, only_if_changed=True)
