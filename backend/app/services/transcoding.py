from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Callable
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.session import SessionLocal
from backend.app.models.entities import (
    AudioStream,
    ExternalSubtitle,
    JobStatus,
    LibraryRoot,
    MediaFile,
    SubtitleStream,
    TranscodeJob,
    TranscodeVariant,
    TranscodeVariantGroup,
    VideoStream,
)
from backend.app.schemas.transcoding import (
    ExternalSubtitlePlan,
    FileTranscodeRead,
    TranscodeCapabilitiesRead,
    TranscodeAttachmentSummary,
    TranscodeEncoderCapability,
    TranscodeFileSummary,
    TranscodeJobPageRead,
    TranscodeJobRead,
    TranscodePlan,
    TranscodeStreamAction,
    TranscodeStreamPlan,
    TranscodeValidationRead,
    TranscodeVariantRead,
)
from backend.app.utils.time import utc_now


HARDWARE_ENCODER_MARKERS = (
    "_nvenc",
    "_qsv",
    "_amf",
    "_videotoolbox",
    "_vaapi",
    "_vulkan",
    "_v4l2m2m",
    "_d3d12va",
    "_mediacodec",
    "_rkmpp",
)
VIDEO_ENCODER_CODECS = {
    "libx264": "h264",
    "h264_nvenc": "h264",
    "h264_qsv": "h264",
    "h264_amf": "h264",
    "h264_videotoolbox": "h264",
    "h264_vaapi": "h264",
    "libx265": "hevc",
    "hevc_nvenc": "hevc",
    "hevc_qsv": "hevc",
    "hevc_amf": "hevc",
    "hevc_videotoolbox": "hevc",
    "hevc_vaapi": "hevc",
    "libsvtav1": "av1",
    "libaom-av1": "av1",
    "av1_nvenc": "av1",
    "av1_qsv": "av1",
    "av1_amf": "av1",
    "av1_vaapi": "av1",
    "libvpx-vp9": "vp9",
    "vp9_qsv": "vp9",
    "vp9_vaapi": "vp9",
    "libvpx": "vp8",
}
AUDIO_ENCODER_CODECS = {
    "aac": "aac",
    "libfdk_aac": "aac",
    "libopus": "opus",
    "opus": "opus",
    "libvorbis": "vorbis",
    "ac3": "ac3",
    "eac3": "eac3",
    "flac": "flac",
    "libmp3lame": "mp3",
}
SUBTITLE_ENCODER_CODECS = {
    "mov_text": "mov_text",
    "srt": "subrip",
    "subrip": "subrip",
    "ass": "ass",
    "webvtt": "webvtt",
}
CONTAINER_FORMATS = {"mkv": "matroska", "mp4": "mp4", "webm": "webm"}
CONTAINER_COMPATIBILITY = {
    "mp4": {
        "video": {"h264", "hevc", "av1", "mpeg4", "mjpeg"},
        "audio": {"aac", "ac3", "eac3", "mp3", "alac"},
        "subtitle": {"mov_text"},
    },
    "webm": {
        "video": {"vp8", "vp9", "av1"},
        "audio": {"opus", "vorbis"},
        "subtitle": {"webvtt"},
    },
}
FILENAME_TOKENS = {
    "resolution",
    "dynRange",
    "codec",
    "audioLanguages",
    "container",
    "videoBitrate",
}
BITMAP_SUBTITLE_CODECS = {"dvb_subtitle", "dvd_subtitle", "hdmv_pgs_subtitle", "pgs", "xsub"}
CAPABILITIES_LOCK = Lock()


class TranscodeValidationError(ValueError):
    def __init__(self, validation: TranscodeValidationRead) -> None:
        self.validation = validation
        super().__init__("; ".join(validation.errors) or "Transcoding plan is invalid")


class TranscodeCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SourcePaths:
    root: Path
    source: Path


def _safe_path_below(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Media path escapes its library root") from exc
    return candidate


def _source_paths(media_file: MediaFile) -> SourcePaths:
    root = Path(media_file.library_root.path if media_file.library_root else media_file.library.path)
    source = _safe_path_below(root, media_file.relative_path)
    return SourcePaths(root=root.resolve(), source=source)


def _is_hardware_encoder(name: str) -> bool:
    return any(marker in name for marker in HARDWARE_ENCODER_MARKERS)


def _hardware_encoder_codec(name: str) -> str | None:
    if not _is_hardware_encoder(name):
        return None
    normalized = name.lower()
    for prefix, codec in (
        ("h264", "h264"),
        ("hevc", "hevc"),
        ("av1", "av1"),
        ("vp9", "vp9"),
        ("vp8", "vp8"),
        ("mpeg2", "mpeg2video"),
        ("mjpeg", "mjpeg"),
    ):
        if normalized.startswith(prefix):
            return codec
    return None


def _test_hardware_encoder(ffmpeg_path: str, encoder: str) -> tuple[bool, str | None]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:d=0.1",
        "-frames:v",
        "1",
        "-c:v",
        encoder,
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    error = (completed.stderr or completed.stdout or "").strip()
    return completed.returncode == 0, error[-1000:] or None


def _encoder_options(ffmpeg_path: str, encoder: str) -> list[str]:
    try:
        completed = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-h", f"encoder={encoder}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    options = {
        match.group(1)
        for line in (completed.stdout or "").splitlines()
        if (match := re.match(r"^\s+-([A-Za-z0-9_]+)\s+<", line))
    }
    return sorted(options)


@lru_cache(maxsize=8)
def _detect_capabilities_cached(ffmpeg_path: str) -> TranscodeCapabilitiesRead:
    try:
        version_result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TranscodeCapabilitiesRead(
            ffmpeg_available=False,
            ffmpeg_path=ffmpeg_path,
            error=str(exc),
        )
    if version_result.returncode != 0:
        return TranscodeCapabilitiesRead(
            ffmpeg_available=False,
            ffmpeg_path=ffmpeg_path,
            error=(version_result.stderr or version_result.stdout or "FFmpeg failed").strip(),
        )
    version_line = (version_result.stdout or "").splitlines()[0] if version_result.stdout else None
    encoder_result = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if encoder_result.returncode != 0:
        return TranscodeCapabilitiesRead(
            ffmpeg_available=True,
            ffmpeg_path=ffmpeg_path,
            version=version_line,
            error=(encoder_result.stderr or "Unable to list FFmpeg encoders").strip(),
        )
    muxer_result = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-muxers"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    muxers: set[str] = set()
    if muxer_result.returncode == 0:
        for line in (muxer_result.stdout or "").splitlines():
            match = re.match(r"^\s*E\s+([A-Za-z0-9_,.-]+)\s", line)
            if match:
                muxers.update(match.group(1).split(","))

    listed_names: set[str] = set()
    for line in (encoder_result.stdout or "").splitlines():
        match = re.match(r"^\s*[A-Z\.]{6}\s+([A-Za-z0-9_.-]+)\s", line)
        if match:
            listed_names.add(match.group(1))
    known = {**VIDEO_ENCODER_CODECS, **AUDIO_ENCODER_CODECS, **SUBTITLE_ENCODER_CODECS}
    for name in listed_names:
        inferred_codec = _hardware_encoder_codec(name)
        if inferred_codec:
            known.setdefault(name, inferred_codec)
    capabilities: list[TranscodeEncoderCapability] = []
    for name in sorted(listed_names & set(known)):
        hardware = _is_hardware_encoder(name)
        tested = False
        available = True
        test_error = None
        if hardware:
            tested = True
            available, test_error = _test_hardware_encoder(ffmpeg_path, name)
        capabilities.append(
            TranscodeEncoderCapability(
                name=name,
                codec=known[name],
                hardware=hardware,
                available=available,
                tested=tested,
                test_error=test_error,
                options=_encoder_options(ffmpeg_path, name)
                if name in VIDEO_ENCODER_CODECS or hardware
                else [],
            )
        )
    return TranscodeCapabilitiesRead(
        ffmpeg_available=True,
        ffmpeg_path=ffmpeg_path,
        version=version_line,
        encoders=capabilities,
        dolby_vision_passthrough=bool({"matroska", "mp4"} & muxers),
    )


def get_transcode_capabilities(settings: Settings, *, refresh: bool = False) -> TranscodeCapabilitiesRead:
    with CAPABILITIES_LOCK:
        if refresh:
            _detect_capabilities_cached.cache_clear()
        return _detect_capabilities_cached(settings.ffmpeg_path).model_copy(deep=True)


def _available_encoder(capabilities: TranscodeCapabilitiesRead, *preferred: str) -> str | None:
    available = {item.name for item in capabilities.encoders if item.available}
    return next((name for name in preferred if name in available), None)


def _default_subtitle_encoder(container: str) -> str:
    if container == "mp4":
        return "mov_text"
    if container == "webm":
        return "webvtt"
    return "srt"


def _profile_plan(media_file: MediaFile, profile: str, capabilities: TranscodeCapabilitiesRead) -> TranscodePlan:
    dynamic_range = "preserve"
    if profile == "compatibility":
        container = "mp4"
        if any((stream.hdr_type or "").lower() not in {"", "sdr"} for stream in media_file.video_streams):
            dynamic_range = "sdr"
        video_encoder = _available_encoder(capabilities, "libx264") or "libx264"
        video_plans = [
            TranscodeStreamPlan(
                stream_index=stream.stream_index,
                action="encode",
                codec="h264",
                encoder=video_encoder,
                crf=20,
                width=min(stream.width or 1920, 1920),
                height=min(stream.height or 1080, 1080),
                preset="medium",
            )
            for stream in media_file.video_streams
        ]
        audio_plans = [
            TranscodeStreamPlan(
                stream_index=stream.stream_index,
                action="encode",
                codec="aac",
                encoder="aac",
                bitrate=192_000 if (stream.channels or 2) <= 2 else 384_000,
                language=stream.language,
            )
            for stream in media_file.audio_streams
        ]
        subtitle_plans = [
            TranscodeStreamPlan(
                stream_index=stream.stream_index,
                action="encode",
                codec="mov_text",
                encoder="mov_text",
                language=stream.language,
            )
            for stream in media_file.subtitle_streams
        ]
    elif profile == "storage":
        container = "mkv"
        video_encoder = _available_encoder(capabilities, "libx265") or "libx265"
        video_plans = [
            TranscodeStreamPlan(
                stream_index=stream.stream_index,
                action="encode",
                codec="hevc",
                encoder=video_encoder,
                crf=22,
                preset="medium",
            )
            for stream in media_file.video_streams
        ]
        audio_plans = [
            TranscodeStreamPlan(stream_index=stream.stream_index, action="copy", language=stream.language)
            for stream in media_file.audio_streams
        ]
        subtitle_plans = [
            TranscodeStreamPlan(stream_index=stream.stream_index, action="copy", language=stream.language)
            for stream in media_file.subtitle_streams
        ]
    else:
        container = "mkv"
        video_encoder = _available_encoder(capabilities, "libsvtav1", "libaom-av1") or "libsvtav1"
        video_plans = [
            TranscodeStreamPlan(
                stream_index=stream.stream_index,
                action="encode",
                codec="av1",
                encoder=video_encoder,
                crf=30,
                preset="6" if video_encoder == "libsvtav1" else None,
            )
            for stream in media_file.video_streams
        ]
        audio_plans = [
            TranscodeStreamPlan(stream_index=stream.stream_index, action="copy", language=stream.language)
            for stream in media_file.audio_streams
        ]
        subtitle_plans = [
            TranscodeStreamPlan(stream_index=stream.stream_index, action="copy", language=stream.language)
            for stream in media_file.subtitle_streams
        ]
    return TranscodePlan(
        profile=profile,
        container=container,
        video_streams=video_plans,
        audio_streams=audio_plans,
        subtitle_streams=subtitle_plans,
        dynamic_range=dynamic_range,
    )


def initial_transcode_profiles(media_file: MediaFile, capabilities: TranscodeCapabilitiesRead) -> dict[str, TranscodePlan]:
    return {
        profile: _profile_plan(media_file, profile, capabilities)
        for profile in ("compatibility", "storage", "modern")
    }


def _stream_codec_map(media_file: MediaFile) -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for stream in media_file.video_streams:
        result[("video", stream.stream_index)] = (stream.codec or "").lower()
    for stream in media_file.audio_streams:
        result[("audio", stream.stream_index)] = (stream.codec or "").lower()
    for stream in media_file.subtitle_streams:
        result[("subtitle", stream.stream_index)] = (stream.codec or "").lower()
    return result


def _encoder_codec(encoder: str | None) -> str | None:
    if not encoder:
        return None
    return ({**VIDEO_ENCODER_CODECS, **AUDIO_ENCODER_CODECS, **SUBTITLE_ENCODER_CODECS}).get(encoder)


def _sanitize_filename(value: str, *, suffix: str) -> str:
    candidate = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .")
    if not candidate:
        candidate = "transcoded"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if candidate.upper() in reserved:
        candidate = f"_{candidate}"
    max_stem = max(32, 240 - len(suffix))
    return candidate[:max_stem].rstrip(" .") or "transcoded"


def _token_values(media_file: MediaFile, plan: TranscodePlan) -> dict[str, str]:
    primary_video = next((item for item in plan.video_streams if item.action != TranscodeStreamAction.drop), None)
    source_video = media_file.video_streams[0] if media_file.video_streams else None
    width = primary_video.width if primary_video and primary_video.width else (source_video.width if source_video else None)
    height = primary_video.height if primary_video and primary_video.height else (source_video.height if source_video else None)
    codec = (
        (primary_video.codec or _encoder_codec(primary_video.encoder))
        if primary_video and primary_video.action == TranscodeStreamAction.encode
        else (source_video.codec if source_video else None)
    )
    languages = sorted(
        {
            (plan_stream.language or source.language or "").strip()
            for plan_stream, source in (
                (plan_stream, next((item for item in media_file.audio_streams if item.stream_index == plan_stream.stream_index), None))
                for plan_stream in plan.audio_streams
                if plan_stream.action != TranscodeStreamAction.drop
            )
            if source is not None and (plan_stream.language or source.language or "").strip()
        }
    )
    bitrate = primary_video.bitrate if primary_video else None
    return {
        "resolution": f"{width}x{height}" if width and height else "",
        "dynRange": plan.dynamic_range if plan.dynamic_range != "preserve" else (media_file.primary_video_hdr_type or ""),
        "codec": (codec or "").upper(),
        "audioLanguages": "+".join(languages),
        "container": plan.container.upper(),
        "videoBitrate": f"{round(bitrate / 1_000_000, 1):g}Mbps" if bitrate else "",
    }


def render_output_filename(media_file: MediaFile, plan: TranscodePlan) -> str:
    unknown_tokens = set(re.findall(r"\{([^{}]+)\}", plan.filename_template)) - FILENAME_TOKENS
    if unknown_tokens:
        raise ValueError(f"Unsupported filename token(s): {', '.join(sorted(unknown_tokens))}")
    rendered = plan.filename_template
    for token, value in _token_values(media_file, plan).items():
        rendered = rendered.replace(f"{{{token}}}", value)
    rendered = re.sub(r"\[\s*[,;|+\-]*\s*\]", "", rendered)
    rendered = re.sub(r"([\[,;|+])\s*([,;|+])", r"\1", rendered)
    rendered = re.sub(r"\s*,\s*(?=\])", "", rendered)
    rendered = re.sub(r"\[\s*,\s*", "[", rendered)
    rendered = re.sub(r"\s+", " ", rendered).strip(" ,;|+-")
    suffix = f".{plan.container}"
    stem = _sanitize_filename(f"{Path(media_file.filename).stem} {rendered}".strip(), suffix=suffix)
    return f"{stem}{suffix}"


def _dynamic_range_filter(dynamic_range: str) -> str | None:
    if dynamic_range == "sdr":
        return "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p"
    if dynamic_range == "hdr10":
        return "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=clip,zscale=p=bt2020:t=smpte2084:m=bt2020nc:r=tv,format=yuv420p10le"
    if dynamic_range == "hlg":
        return "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=clip,zscale=p=bt2020:t=arib-std-b67:m=bt2020nc:r=tv,format=yuv420p10le"
    return None


def _output_codec(kind: str, decision: TranscodeStreamPlan, source_codec: str) -> str:
    if decision.action in {TranscodeStreamAction.keep, TranscodeStreamAction.copy}:
        return source_codec
    return (decision.codec or _encoder_codec(decision.encoder) or "").lower()


def _quote_command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _append_stream_options(
    arguments: list[str],
    kind_letter: str,
    output_index: int,
    decision: TranscodeStreamPlan,
    source: VideoStream | AudioStream | SubtitleStream,
    dynamic_range: str,
) -> None:
    specifier = f"{kind_letter}:{output_index}"
    if decision.action in {TranscodeStreamAction.keep, TranscodeStreamAction.copy}:
        arguments.extend([f"-c:{specifier}", "copy"])
    else:
        encoder = decision.encoder or decision.codec
        if encoder:
            arguments.extend([f"-c:{specifier}", encoder])
        if decision.bitrate:
            arguments.extend([f"-b:{specifier}", str(decision.bitrate)])
        if decision.cq is not None:
            arguments.extend([f"-cq:{specifier}", f"{decision.cq:g}"])
        elif decision.crf is not None:
            arguments.extend([f"-crf:{specifier}", f"{decision.crf:g}"])
        if decision.width or decision.height:
            width = decision.width or -2
            height = decision.height or -2
            arguments.extend([f"-filter:{specifier}", f"scale={width}:{height}:force_original_aspect_ratio=decrease"])
        effective_pixel_format = decision.pixel_format
        if kind_letter == "v":
            dynamic_filter = _dynamic_range_filter(dynamic_range)
            if dynamic_filter:
                filter_key = f"-filter:{specifier}"
                if filter_key in arguments:
                    position = arguments.index(filter_key)
                    arguments[position + 1] = f"{arguments[position + 1]},{dynamic_filter}"
                else:
                    arguments.extend([filter_key, dynamic_filter])
            if dynamic_range == "hdr10":
                arguments.extend([f"-color_primaries:{specifier}", "bt2020", f"-color_trc:{specifier}", "smpte2084", f"-colorspace:{specifier}", "bt2020nc"])
            elif dynamic_range == "hlg":
                arguments.extend([f"-color_primaries:{specifier}", "bt2020", f"-color_trc:{specifier}", "arib-std-b67", f"-colorspace:{specifier}", "bt2020nc"])
            elif dynamic_range == "preserve":
                if source.color_primaries:
                    arguments.extend([f"-color_primaries:{specifier}", source.color_primaries])
                if source.color_transfer:
                    arguments.extend([f"-color_trc:{specifier}", source.color_transfer])
                if source.color_space:
                    arguments.extend([f"-colorspace:{specifier}", source.color_space])
                source_hdr = (source.hdr_type or "").lower()
                if not effective_pixel_format and source_hdr not in {"", "sdr"} and (source.bit_depth or 0) >= 10:
                    effective_pixel_format = source.pix_fmt or "yuv420p10le"
        if decision.frame_rate:
            arguments.extend([f"-r:{specifier}", f"{decision.frame_rate:g}"])
        if effective_pixel_format:
            arguments.extend([f"-pix_fmt:{specifier}", effective_pixel_format])
        if decision.profile:
            arguments.extend([f"-profile:{specifier}", decision.profile])
        if decision.level:
            arguments.extend([f"-level:{specifier}", decision.level])
        if decision.preset:
            arguments.extend([f"-preset:{specifier}", decision.preset])
        if decision.gop_size:
            arguments.extend([f"-g:{specifier}", str(decision.gop_size)])
    language = decision.language or getattr(source, "language", None)
    if language:
        arguments.extend([f"-metadata:s:{specifier}", f"language={language}"])
    if decision.title:
        arguments.extend([f"-metadata:s:{specifier}", f"title={decision.title}"])
    disposition: list[str] = []
    if getattr(source, "default_flag", False):
        disposition.append("default")
    if getattr(source, "forced_flag", False):
        disposition.append("forced")
    arguments.extend([f"-disposition:{specifier}", "+".join(disposition) if disposition else "0"])


def validate_transcode_plan(
    db: Session,
    settings: Settings,
    media_file: MediaFile,
    plan: TranscodePlan,
    *,
    output_path_override: Path | None = None,
) -> TranscodeValidationRead:
    paths = _source_paths(media_file)
    capabilities = get_transcode_capabilities(settings)
    errors: list[str] = []
    warnings: list[str] = []
    kept: list[str] = []
    changed: list[str] = []
    removed: list[str] = []
    added: list[str] = []
    if not paths.source.exists() or not paths.source.is_file():
        errors.append("The source file no longer exists")
    if not media_file.video_streams:
        errors.append("Transcoding is only available for files with a regular video stream")
    if not capabilities.ffmpeg_available:
        errors.append(capabilities.error or "FFmpeg is unavailable")

    try:
        output_filename = render_output_filename(media_file, plan)
    except ValueError as exc:
        output_filename = f"{Path(media_file.filename).stem}.transcoded.{plan.container}"
        errors.append(str(exc))
    output_path = output_path_override or (paths.source.parent / output_filename)
    try:
        output_path.resolve().relative_to(paths.root)
    except ValueError:
        errors.append("The output path escapes the library root")
    if output_path.resolve() == paths.source.resolve():
        errors.append("The output path must differ from the source path")
    if output_path.exists():
        errors.append("The output file already exists and will not be overwritten")
    active_collision = db.scalar(
        select(TranscodeJob.id).where(
            TranscodeJob.output_path_snapshot == str(output_path),
            TranscodeJob.status.in_([JobStatus.queued, JobStatus.running]),
        ).limit(1)
    )
    if active_collision is not None:
        errors.append("Another active transcoding job already targets this output path")

    source_by_kind = {
        "video": {item.stream_index: item for item in media_file.video_streams},
        "audio": {item.stream_index: item for item in media_file.audio_streams},
        "subtitle": {item.stream_index: item for item in media_file.subtitle_streams},
    }
    plan_by_kind = {
        "video": plan.video_streams,
        "audio": plan.audio_streams,
        "subtitle": plan.subtitle_streams,
    }
    available_encoders = {item.name: item for item in capabilities.encoders}
    arguments = [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-n", "-i", str(paths.source)]
    external_rows = {item.id: item for item in media_file.external_subtitles}
    selected_external: list[tuple[ExternalSubtitlePlan, ExternalSubtitle, Path]] = []
    for external in plan.external_subtitles:
        if external.action == "drop":
            continue
        row = external_rows.get(external.subtitle_id)
        if row is None:
            errors.append(f"External subtitle {external.subtitle_id} does not belong to this file")
            continue
        external_path = (paths.source.parent / row.path).resolve()
        try:
            external_path.relative_to(paths.root)
        except ValueError:
            errors.append(f"External subtitle escapes the library root: {row.path}")
            continue
        if not external_path.exists():
            errors.append(f"External subtitle no longer exists: {row.path}")
            continue
        arguments.extend(["-i", str(external_path)])
        selected_external.append((external, row, external_path))
        added.append(f"external subtitle {row.path}")

    output_counts = {"video": 0, "audio": 0, "subtitle": 0}
    kind_letter = {"video": "v", "audio": "a", "subtitle": "s"}
    for kind, decisions in plan_by_kind.items():
        seen: set[int] = set()
        for decision in decisions:
            if decision.stream_index in seen:
                errors.append(f"Stream {decision.stream_index} is selected more than once for {kind}")
                continue
            seen.add(decision.stream_index)
            source = source_by_kind[kind].get(decision.stream_index)
            label = f"{kind} stream {decision.stream_index}"
            if source is None:
                errors.append(f"{label} does not exist in the source")
                continue
            if decision.action == TranscodeStreamAction.drop:
                removed.append(label)
                continue
            source_codec = (source.codec or "").lower()
            output_codec = _output_codec(kind, decision, source_codec)
            if decision.action == TranscodeStreamAction.encode:
                encoder = decision.encoder or decision.codec
                capability = available_encoders.get(encoder or "")
                if capability is None:
                    errors.append(f"Requested encoder is not provided by this FFmpeg build: {encoder or 'none'}")
                elif not capability.available:
                    errors.append(f"Requested hardware encoder failed its capability test: {encoder}")
                if decision.codec and _encoder_codec(encoder) not in {None, decision.codec}:
                    errors.append(f"Encoder {encoder} does not produce requested codec {decision.codec}")
                if kind == "subtitle" and source_codec in BITMAP_SUBTITLE_CODECS and output_codec in {
                    "ass",
                    "mov_text",
                    "srt",
                    "subrip",
                    "webvtt",
                }:
                    errors.append(
                        f"Bitmap subtitle stream {decision.stream_index} cannot be converted to text codec {output_codec}"
                    )
                changed.append(label)
            else:
                kept.append(label)
            compatibility = CONTAINER_COMPATIBILITY.get(plan.container)
            if compatibility and output_codec not in compatibility[kind]:
                errors.append(f"Codec {output_codec or 'unknown'} is not supported for {kind} in {plan.container}")
            arguments.extend(["-map", f"0:{decision.stream_index}"])
            _append_stream_options(
                arguments,
                kind_letter[kind],
                output_counts[kind],
                decision,
                source,
                plan.dynamic_range,
            )
            output_counts[kind] += 1
        for stream_index in set(source_by_kind[kind]) - seen:
            removed.append(f"{kind} stream {stream_index}")

    for input_offset, (decision, row, _path) in enumerate(selected_external, start=1):
        output_index = output_counts["subtitle"]
        arguments.extend(["-map", f"{input_offset}:0"])
        codec = decision.codec or _default_subtitle_encoder(plan.container)
        if decision.action == "copy":
            codec = "copy"
        elif codec not in available_encoders:
            errors.append(f"Requested subtitle encoder is unavailable: {codec}")
        if (row.format or "").lower() in BITMAP_SUBTITLE_CODECS and codec in {
            "ass",
            "mov_text",
            "srt",
            "subrip",
            "webvtt",
        }:
            errors.append(f"Bitmap external subtitle {row.path} cannot be converted to text codec {codec}")
        arguments.extend([f"-c:s:{output_index}", codec])
        language = decision.language or row.language
        if language:
            arguments.extend([f"-metadata:s:s:{output_index}", f"language={language}"])
        if decision.title:
            arguments.extend([f"-metadata:s:s:{output_index}", f"title={decision.title}"])
        output_counts["subtitle"] += 1

    if not output_counts["video"]:
        errors.append("At least one video stream must be kept or encoded")
    source_hdr = (media_file.primary_video_hdr_type or "").lower()
    if plan.dynamic_range == "dolby_vision":
        video_decisions = [item for item in plan.video_streams if item.action != TranscodeStreamAction.drop]
        if not capabilities.dolby_vision_passthrough:
            errors.append("This FFmpeg build has no verified Dolby Vision passthrough container")
        if "dolby" not in source_hdr:
            errors.append("Dolby Vision can only be preserved from a detected Dolby Vision source")
        if (media_file.primary_video_codec or "").lower() not in {"hevc", "h265"}:
            errors.append("Dolby Vision passthrough requires a detected HEVC video stream")
        if plan.container not in {"mkv", "mp4"}:
            errors.append("Dolby Vision passthrough is only offered for MKV or MP4")
        if any(item.action not in {TranscodeStreamAction.keep, TranscodeStreamAction.copy} for item in video_decisions):
            errors.append("MediaLyze does not synthesize Dolby Vision metadata; Dolby Vision requires video stream copy")
    elif "dolby" in source_hdr and plan.dynamic_range == "preserve" and any(
        item.action == TranscodeStreamAction.encode for item in plan.video_streams
    ):
        warnings.append("Encoding the video stream does not preserve Dolby Vision RPU metadata; choose SDR, HDR10, HLG, or stream copy")
    elif "hdr10+" in source_hdr and plan.dynamic_range == "preserve" and any(
        item.action == TranscodeStreamAction.encode for item in plan.video_streams
    ):
        warnings.append("Encoding may not preserve HDR10+ dynamic metadata; use video stream copy when exact preservation is required")
    if plan.dynamic_range != "preserve" and any(
        item.action in {TranscodeStreamAction.keep, TranscodeStreamAction.copy}
        for item in plan.video_streams
        if item.action != TranscodeStreamAction.drop
    ):
        errors.append("Dynamic-range conversion requires encoding every selected video stream")

    if plan.attachments == "keep" and plan.container == "mkv":
        arguments.extend(["-map", "0:t?", "-c:t", "copy"])
    elif plan.attachments == "keep" and plan.container != "mkv":
        warnings.append(f"Attachments are not copied to {plan.container}")
    if plan.cover == "keep" and media_file.has_embedded_cover:
        if media_file.embedded_cover_stream_index is None:
            warnings.append("The embedded cover has no source stream index and cannot be copied")
        elif plan.container not in {"mkv", "mp4"}:
            warnings.append(f"Embedded covers are not copied to {plan.container}")
        else:
            cover_output_index = output_counts["video"]
            arguments.extend(
                [
                    "-map",
                    f"0:{media_file.embedded_cover_stream_index}",
                    f"-c:v:{cover_output_index}",
                    "copy",
                    f"-disposition:v:{cover_output_index}",
                    "attached_pic",
                ]
            )
            added.append("embedded cover")
    arguments.extend(["-map_metadata", "0" if plan.metadata == "keep" else "-1"])
    arguments.extend(["-map_chapters", "0" if plan.chapters == "keep" else "-1"])
    arguments.extend(["-progress", "pipe:1", "-stats_period", "0.5", "-f", CONTAINER_FORMATS[plan.container], str(output_path)])

    hardware = [item.name for item in capabilities.encoders if item.hardware and item.available]
    return TranscodeValidationRead(
        valid=not errors,
        output_path=str(output_path),
        output_filename=output_filename,
        normalized_plan=plan,
        ffmpeg_arguments=arguments,
        ffmpeg_command=_quote_command(arguments),
        kept_streams=kept,
        changed_streams=changed,
        removed_streams=removed,
        added_streams=added,
        warnings=warnings,
        errors=errors,
        detected_hardware_encoders=hardware,
    )


def _group_for_source(db: Session, media_file: MediaFile) -> TranscodeVariantGroup | None:
    group = db.scalar(
        select(TranscodeVariantGroup).where(TranscodeVariantGroup.original_file_id == media_file.id).limit(1)
    )
    if group is not None:
        return group
    return db.scalar(
        select(TranscodeVariantGroup)
        .join(TranscodeVariant, TranscodeVariant.group_id == TranscodeVariantGroup.id)
        .where(TranscodeVariant.output_file_id == media_file.id)
        .limit(1)
    )


def queue_transcode_job(
    db: Session,
    settings: Settings,
    media_file: MediaFile,
    plan: TranscodePlan,
) -> tuple[TranscodeJob, TranscodeValidationRead]:
    validation = validate_transcode_plan(db, settings, media_file, plan)
    if not validation.valid:
        raise TranscodeValidationError(validation)
    paths = _source_paths(media_file)
    source_stat = paths.source.stat()
    group = _group_for_source(db, media_file)
    if group is None:
        group = TranscodeVariantGroup(
            library_id=media_file.library_id,
            original_file_id=media_file.id,
            original_library_root_id=media_file.library_root_id,
            original_relative_path=media_file.relative_path,
            original_filename=media_file.filename,
        )
        db.add(group)
        db.flush()
    output_path = Path(validation.output_path)
    temporary_path = output_path.with_name(
        f".{output_path.stem}.medialyze-{uuid4().hex}{output_path.suffix}.part"
    )
    actual_validation = validate_transcode_plan(
        db,
        settings,
        media_file,
        plan,
        output_path_override=temporary_path,
    )
    if not actual_validation.valid:
        raise TranscodeValidationError(actual_validation)
    actual_arguments = list(actual_validation.ffmpeg_arguments)
    job = TranscodeJob(
        group_id=group.id,
        library_id=media_file.library_id,
        source_file_id=media_file.id,
        status=JobStatus.queued,
        profile=plan.profile,
        plan_version=plan.version,
        plan=plan.model_dump(mode="json"),
        ffmpeg_arguments=actual_arguments,
        ffmpeg_command=_quote_command(actual_arguments),
        warnings=validation.warnings,
        source_path_snapshot=str(paths.source),
        source_size_snapshot=source_stat.st_size,
        source_mtime_snapshot=source_stat.st_mtime,
        output_path_snapshot=validation.output_path,
        output_relative_path=output_path.relative_to(paths.root).as_posix(),
        temporary_path=str(temporary_path),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, validation


def _publish_without_overwrite(temporary_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise FileExistsError("The output file appeared while transcoding and was not overwritten")
    try:
        os.link(temporary_path, output_path)
    except FileExistsError:
        raise FileExistsError("The output file appeared while transcoding and was not overwritten") from None
    except OSError:
        if os.name != "nt":
            raise RuntimeError("The target filesystem cannot atomically publish the transcoded file without overwrite") from None
        os.rename(temporary_path, output_path)
        return
    temporary_path.unlink()


def _remove_temporary_output(temporary_path: Path, output_path: Path) -> None:
    expected_prefix = f".{output_path.stem}.medialyze-"
    expected_suffix = f"{output_path.suffix}.part"
    if (
        temporary_path.parent.resolve() != output_path.parent.resolve()
        or not temporary_path.name.startswith(expected_prefix)
        or not temporary_path.name.endswith(expected_suffix)
    ):
        return
    temporary_path.unlink(missing_ok=True)


def _verify_job_paths(db: Session, job: TranscodeJob, source: Path, output: Path, temporary: Path) -> None:
    group = db.get(TranscodeVariantGroup, job.group_id)
    root = db.get(LibraryRoot, group.original_library_root_id) if group and group.original_library_root_id else None
    if root is None:
        raise ValueError("The original library root no longer exists")
    resolved_root = Path(root.path).resolve()
    for label, candidate in (("source", source), ("output", output), ("temporary output", temporary)):
        try:
            candidate.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"The {label} path escapes the library root") from exc
    if source.resolve() == output.resolve() or source.resolve() == temporary.resolve():
        raise ValueError("Source and output paths must be different")


def _update_progress(job: TranscodeJob, key: str, value: str, duration: float) -> None:
    if key in {"out_time_us", "out_time_ms"}:
        try:
            raw = float(value)
        except ValueError:
            return
        seconds = raw / 1_000_000
        job.processed_seconds = max(job.processed_seconds, seconds)
        if duration > 0:
            job.progress_percent = min(99.9, max(0.0, seconds / duration * 100))
    elif key == "speed":
        job.speed = value or None
        try:
            multiplier = float(value.rstrip("x"))
        except (TypeError, ValueError):
            multiplier = 0.0
        remaining = max(0.0, duration - job.processed_seconds)
        job.eta_seconds = remaining / multiplier if multiplier > 0 else None


def execute_transcode_job(
    job_id: int,
    *,
    is_cancel_requested: Callable[[int], bool],
) -> int:
    db = SessionLocal()
    process: subprocess.Popen[str] | None = None
    temporary_path: Path | None = None
    try:
        job = db.get(TranscodeJob, job_id)
        if job is None:
            raise ValueError("Transcoding job not found")
        if job.status != JobStatus.queued:
            return job.library_id
        job.status = JobStatus.running
        job.started_at = utc_now()
        job.error = None
        db.commit()
        source_path = Path(job.source_path_snapshot)
        output_path = Path(job.output_path_snapshot)
        temporary_path = Path(job.temporary_path or "")
        if not temporary_path.name:
            raise ValueError("Transcoding job has no temporary output path")
        _verify_job_paths(db, job, source_path, output_path, temporary_path)
        current_stat = source_path.stat()
        if current_stat.st_size != job.source_size_snapshot or current_stat.st_mtime != job.source_mtime_snapshot:
            raise ValueError("The source file changed before transcoding started")
        if output_path.exists():
            raise FileExistsError("The output file already exists and was not overwritten")
        _remove_temporary_output(temporary_path, output_path)
        duration = 0.0
        source = db.get(MediaFile, job.source_file_id) if job.source_file_id else None
        if source is not None:
            duration = float(source.duration_seconds or 0.0)
        process = subprocess.Popen(
            list(job.ffmpeg_arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        last_commit = utc_now()
        if process.stdout is not None:
            for raw_line in process.stdout:
                if is_cancel_requested(job_id):
                    process.terminate()
                    raise TranscodeCancelled("Transcoding was canceled")
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                _update_progress(job, key, value, duration)
                now = utc_now()
                if (now - last_commit).total_seconds() >= 0.5 or key == "progress":
                    db.commit()
                    last_commit = now
        stderr = process.stderr.read() if process.stderr is not None else ""
        return_code = process.wait()
        if is_cancel_requested(job_id):
            raise TranscodeCancelled("Transcoding was canceled")
        if return_code != 0:
            raise RuntimeError((stderr or f"FFmpeg exited with code {return_code}").strip()[-32000:])
        current_stat = source_path.stat()
        if current_stat.st_size != job.source_size_snapshot or current_stat.st_mtime != job.source_mtime_snapshot:
            raise ValueError("The source file changed while transcoding; the temporary result was discarded")
        if not temporary_path.exists() or temporary_path.stat().st_size <= 0:
            raise RuntimeError("FFmpeg completed without producing a valid output file")
        _publish_without_overwrite(temporary_path, output_path)
        variant = TranscodeVariant(
            group_id=job.group_id,
            job_id=job.id,
            original_file_id=job.source_file_id,
            library_root_id=source.library_root_id if source else None,
            output_relative_path=job.output_relative_path,
            output_filename=output_path.name,
            source_path_snapshot=job.source_path_snapshot,
            output_path_snapshot=job.output_path_snapshot,
            analysis_status="awaiting_analysis",
        )
        db.add(variant)
        job.status = JobStatus.completed
        job.progress_percent = 100.0
        job.processed_seconds = duration or job.processed_seconds
        job.eta_seconds = 0.0
        job.finished_at = utc_now()
        db.commit()
        return job.library_id
    except TranscodeCancelled as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        job = db.get(TranscodeJob, job_id)
        if job is not None:
            job.status = JobStatus.canceled
            job.error = str(exc)
            job.finished_at = utc_now()
            db.commit()
        return job.library_id if job is not None else 0
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
        job = db.get(TranscodeJob, job_id)
        if job is not None:
            job.status = JobStatus.failed
            job.error = (str(exc) or exc.__class__.__name__)[-32000:]
            job.finished_at = utc_now()
            db.commit()
            return job.library_id
        raise
    finally:
        if temporary_path is not None and "output_path" in locals():
            _remove_temporary_output(temporary_path, output_path)
        db.close()


def cancel_transcode_job(db: Session, job_id: int) -> TranscodeJob:
    job = db.get(TranscodeJob, job_id)
    if job is None:
        raise ValueError("Transcoding job not found")
    if job.status == JobStatus.queued:
        job.status = JobStatus.canceled
        job.finished_at = utc_now()
        if job.temporary_path:
            _remove_temporary_output(Path(job.temporary_path), Path(job.output_path_snapshot))
        db.commit()
        db.refresh(job)
    return job


def recover_orphaned_transcode_jobs(db: Session) -> int:
    jobs = db.scalars(
        select(TranscodeJob).where(TranscodeJob.status.in_([JobStatus.queued, JobStatus.running]))
    ).all()
    finished = utc_now()
    for job in jobs:
        job.status = JobStatus.canceled
        job.error = "Canceled during startup recovery"
        job.finished_at = finished
        if job.temporary_path:
            _remove_temporary_output(Path(job.temporary_path), Path(job.output_path_snapshot))
    if jobs:
        db.commit()
    return len(jobs)


def reconcile_transcode_variants(db: Session, library_id: int) -> int:
    variants = db.scalars(
        select(TranscodeVariant)
        .join(TranscodeVariantGroup, TranscodeVariant.group_id == TranscodeVariantGroup.id)
        .where(TranscodeVariantGroup.library_id == library_id)
    ).all()
    reconciled = 0
    dirty = False
    for variant in variants:
        variant_changed = False
        media_file = db.get(MediaFile, variant.output_file_id) if variant.output_file_id else None
        if media_file is None:
            media_file = db.scalar(
                select(MediaFile).where(
                    MediaFile.library_id == library_id,
                    MediaFile.library_root_id == variant.library_root_id,
                    MediaFile.relative_path == variant.output_relative_path,
                ).limit(1)
            )
        if media_file is None:
            if variant.analysis_status != "awaiting_analysis":
                variant.analysis_status = "awaiting_analysis"
                variant_changed = True
                dirty = True
                reconciled += 1
            continue
        next_status = "ready" if media_file.scan_status.value == "ready" else media_file.scan_status.value
        if variant.output_file_id != media_file.id or variant.analysis_status != next_status:
            variant.output_file_id = media_file.id
            variant.analysis_status = next_status
            variant_changed = True
            dirty = True
        job = db.get(TranscodeJob, variant.job_id) if variant.job_id else None
        if job is not None and job.result_file_id != media_file.id:
            job.result_file_id = media_file.id
            variant_changed = True
            dirty = True
        if variant_changed:
            reconciled += 1
    if dirty:
        db.commit()
    return reconciled


def _file_summary(media_file: MediaFile) -> TranscodeFileSummary:
    return TranscodeFileSummary(
        id=media_file.id,
        filename=media_file.filename,
        relative_path=media_file.relative_path,
        size_bytes=media_file.size_bytes,
        duration_seconds=media_file.duration_seconds,
        width=media_file.primary_video_width,
        height=media_file.primary_video_height,
        dynamic_range=media_file.primary_video_hdr_type,
        video_codec=media_file.primary_video_codec,
        audio_codecs=sorted({item.codec for item in media_file.audio_streams if item.codec}),
        audio_languages=sorted({item.language for item in media_file.audio_streams if item.language}),
    )


def _attachment_summaries(media_file: MediaFile) -> list[TranscodeAttachmentSummary]:
    payload = media_file.raw_ffprobe_json if isinstance(media_file.raw_ffprobe_json, dict) else {}
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        return []
    attachments: list[TranscodeAttachmentSummary] = []
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "attachment":
            continue
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        normalized_tags = {str(key).lower(): str(value) for key, value in tags.items()}
        try:
            stream_index = int(stream.get("index"))
        except (TypeError, ValueError):
            continue
        attachments.append(
            TranscodeAttachmentSummary(
                stream_index=stream_index,
                codec=str(stream.get("codec_name")) if stream.get("codec_name") else None,
                filename=normalized_tags.get("filename"),
                mimetype=normalized_tags.get("mimetype"),
                title=normalized_tags.get("title"),
            )
        )
    return attachments


def serialize_transcode_job(job: TranscodeJob) -> TranscodeJobRead:
    payload = TranscodeJobRead.model_validate(job)
    payload.status = job.status.value if hasattr(job.status, "value") else str(job.status)
    return payload


def _serialize_variant(db: Session, variant: TranscodeVariant) -> TranscodeVariantRead:
    payload = TranscodeVariantRead.model_validate(variant)
    if variant.output_file_id:
        media_file = db.get(MediaFile, variant.output_file_id)
        if media_file is not None:
            payload.file = _file_summary(media_file)
    return payload


def get_file_transcode(db: Session, settings: Settings, media_file: MediaFile) -> FileTranscodeRead:
    capabilities = get_transcode_capabilities(settings)
    groups = list(
        db.scalars(
            select(TranscodeVariantGroup).where(
                or_(
                    TranscodeVariantGroup.original_file_id == media_file.id,
                    TranscodeVariantGroup.id.in_(
                        select(TranscodeVariant.group_id).where(TranscodeVariant.output_file_id == media_file.id)
                    ),
                )
            )
        )
    )
    group_ids = [item.id for item in groups]
    variants = list(
        db.scalars(
            select(TranscodeVariant)
            .where(TranscodeVariant.group_id.in_(group_ids or [-1]))
            .order_by(TranscodeVariant.created_at.desc())
        )
    )
    jobs = list(
        db.scalars(
            select(TranscodeJob)
            .where(TranscodeJob.group_id.in_(group_ids or [-1]))
            .order_by(TranscodeJob.created_at.desc())
        )
    )
    original = media_file
    if groups and groups[0].original_file_id:
        original = db.get(MediaFile, groups[0].original_file_id) or media_file
    return FileTranscodeRead(
        original=_file_summary(original),
        profiles=initial_transcode_profiles(media_file, capabilities),
        attachments=_attachment_summaries(media_file),
        variants=[_serialize_variant(db, item) for item in variants],
        jobs=[serialize_transcode_job(item) for item in jobs],
    )


def list_transcode_jobs(
    db: Session,
    *,
    active_only: bool = False,
    library_id: int | None = None,
    status: JobStatus | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> TranscodeJobPageRead:
    filters = []
    if active_only:
        filters.append(TranscodeJob.status.in_([JobStatus.queued, JobStatus.running]))
    if library_id is not None:
        filters.append(TranscodeJob.library_id == library_id)
    if status is not None:
        filters.append(TranscodeJob.status == status)
    effective_start = func.coalesce(TranscodeJob.started_at, TranscodeJob.created_at)
    if started_after is not None:
        filters.append(effective_start >= started_after)
    if started_before is not None:
        filters.append(effective_start <= started_before)
    total = int(db.scalar(select(func.count(TranscodeJob.id)).where(*filters)) or 0)
    jobs = db.scalars(
        select(TranscodeJob)
        .where(*filters)
        .order_by(TranscodeJob.created_at.desc(), TranscodeJob.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return TranscodeJobPageRead(items=[serialize_transcode_job(item) for item in jobs], total=total)
