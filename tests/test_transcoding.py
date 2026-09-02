from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.models.entities import (
    AudioStream,
    ExternalSubtitle,
    JobStatus,
    Library,
    LibraryRoot,
    LibraryType,
    MediaFile,
    ScanJob,
    ScanTriggerSource,
    ScanMode,
    ScanStatus,
    SubtitleStream,
    TranscodeJob,
    TranscodeVariant,
    VideoStream,
)
from backend.app.schemas.transcoding import (
    ExternalSubtitlePlan,
    TranscodeCapabilitiesRead,
    TranscodeEncoderCapability,
    TranscodePlan,
    TranscodeStreamPlan,
)
from backend.app.services import transcoding
from backend.app.services.history_retention import _prune_transcode_history
from backend.app.services.scanner import queue_scan_job
from backend.app.utils.time import utc_now


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_mode="desktop",
        config_path=tmp_path / "config",
        media_root=tmp_path / "media",
        ffmpeg_path="ffmpeg-test",
    )


def _capabilities() -> TranscodeCapabilitiesRead:
    return TranscodeCapabilitiesRead(
        ffmpeg_available=True,
        ffmpeg_path="ffmpeg-test",
        version="ffmpeg version test",
        encoders=[
            TranscodeEncoderCapability(name="libx264", codec="h264"),
            TranscodeEncoderCapability(name="libx265", codec="hevc"),
            TranscodeEncoderCapability(name="libsvtav1", codec="av1"),
            TranscodeEncoderCapability(name="aac", codec="aac"),
            TranscodeEncoderCapability(name="mov_text", codec="mov_text"),
            TranscodeEncoderCapability(name="srt", codec="subrip"),
            TranscodeEncoderCapability(
                name="h264_nvenc",
                codec="h264",
                hardware=True,
                tested=True,
                available=False,
                test_error="no device",
            ),
        ],
    )


def _media_file(db, tmp_path: Path) -> MediaFile:
    root_path = tmp_path / "media" / "Movies"
    root_path.mkdir(parents=True)
    source_path = root_path / "Movie.mkv"
    source_path.write_bytes(b"source-video")
    source_stat = source_path.stat()
    library = Library(
        name="Movies",
        path=str(root_path),
        type=LibraryType.movies,
        scan_mode=ScanMode.manual,
        scan_config={},
        quality_profile={},
    )
    db.add(library)
    db.flush()
    root = LibraryRoot(
        library_id=library.id,
        path=str(root_path),
        display_name="Movies",
        path_key=str(root_path).lower(),
    )
    db.add(root)
    db.flush()
    media_file = MediaFile(
        library_id=library.id,
        library_root_id=root.id,
        relative_path="Movie.mkv",
        filename="Movie.mkv",
        extension="mkv",
        size_bytes=source_stat.st_size,
        mtime=source_stat.st_mtime,
        duration_seconds=120,
        primary_video_codec="hevc",
        primary_video_width=3840,
        primary_video_height=2160,
        primary_video_hdr_type="HDR10",
        scan_status=ScanStatus.ready,
    )
    db.add(media_file)
    db.flush()
    db.add_all(
        [
            VideoStream(
                media_file_id=media_file.id,
                stream_index=0,
                codec="hevc",
                width=3840,
                height=2160,
                hdr_type="HDR10",
            ),
            AudioStream(
                media_file_id=media_file.id,
                stream_index=1,
                codec="aac",
                channels=2,
                language="en",
                default_flag=True,
            ),
            SubtitleStream(
                media_file_id=media_file.id,
                stream_index=2,
                codec="subrip",
                language="de",
                default_flag=True,
            ),
            ExternalSubtitle(
                media_file_id=media_file.id,
                path="Movie.en.srt",
                language="en",
                format="srt",
            ),
        ]
    )
    (root_path / "Movie.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    db.commit()
    return db.get(MediaFile, media_file.id)


def _compatibility_plan() -> TranscodePlan:
    return TranscodePlan(
        profile="compatibility",
        container="mp4",
        video_streams=[
            TranscodeStreamPlan(
                stream_index=0,
                action="encode",
                codec="h264",
                encoder="libx264",
                crf=20,
                width=1920,
                height=1080,
            )
        ],
        audio_streams=[
            TranscodeStreamPlan(
                stream_index=1,
                action="encode",
                codec="aac",
                encoder="aac",
                bitrate=192_000,
            )
        ],
        subtitle_streams=[
            TranscodeStreamPlan(
                stream_index=2,
                action="encode",
                codec="mov_text",
                encoder="mov_text",
            )
        ],
    )


def test_plan_rejects_raw_or_unknown_ffmpeg_arguments() -> None:
    payload = _compatibility_plan().model_dump(mode="json")
    payload["raw_arguments"] = ["-y", "-f", "null"]

    try:
        TranscodePlan.model_validate(payload)
    except ValidationError as exc:
        assert "raw_arguments" in str(exc)
    else:
        raise AssertionError("Unknown raw FFmpeg arguments were accepted")


def test_validation_builds_explicit_maps_and_clean_filename(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, _compatibility_plan())

    assert validation.valid is True
    assert validation.output_filename == "Movie [1920x1080, HDR10, H264] [en].mp4"
    assert validation.ffmpeg_arguments.count("-map") == 3
    assert "0:0" in validation.ffmpeg_arguments
    assert "0:1" in validation.ffmpeg_arguments
    assert "0:2" in validation.ffmpeg_arguments
    assert "-c:v:0" in validation.ffmpeg_arguments
    assert "libx264" in validation.ffmpeg_arguments
    assert validation.ffmpeg_arguments[0] == "ffmpeg-test"
    assert "shell" not in validation.ffmpeg_command.lower()


def test_validation_keeps_crf_and_hardware_cq_distinct(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    capabilities = _capabilities()
    hardware = next(item for item in capabilities.encoders if item.name == "h264_nvenc")
    hardware.available = True
    hardware.test_error = None
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: capabilities)
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        plan = _compatibility_plan()
        plan.video_streams[0].encoder = "h264_nvenc"
        plan.video_streams[0].crf = None
        plan.video_streams[0].cq = 21
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan)

    assert validation.valid is True
    assert "-cq:v:0" in validation.ffmpeg_arguments
    assert "-crf:v:0" not in validation.ffmpeg_arguments


def test_hdr_profiles_tonemap_for_compatibility_and_preserve_static_signaling(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    capabilities = _capabilities()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: capabilities)
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        video = media_file.video_streams[0]
        video.bit_depth = 10
        video.pix_fmt = "yuv420p10le"
        video.color_primaries = "bt2020"
        video.color_transfer = "smpte2084"
        video.color_space = "bt2020nc"
        compatibility = transcoding.initial_transcode_profiles(media_file, capabilities)["compatibility"]
        storage = transcoding.initial_transcode_profiles(media_file, capabilities)["storage"]
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, storage)

    assert compatibility.dynamic_range == "sdr"
    assert "-color_primaries:v:0" in validation.ffmpeg_arguments
    assert "bt2020" in validation.ffmpeg_arguments
    assert "-color_trc:v:0" in validation.ffmpeg_arguments
    assert "smpte2084" in validation.ffmpeg_arguments
    assert "-pix_fmt:v:0" in validation.ffmpeg_arguments
    assert "yuv420p10le" in validation.ffmpeg_arguments


def test_file_transcode_exposes_ffprobe_attachment_breakdown(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        media_file.raw_ffprobe_json = {
            "streams": [
                {
                    "index": 4,
                    "codec_type": "attachment",
                    "codec_name": "ttf",
                    "tags": {
                        "FILENAME": "Poster Font.ttf",
                        "MIMETYPE": "application/x-truetype-font",
                        "title": "Poster Font",
                    },
                }
            ]
        }
        db.commit()
        payload = transcoding.get_file_transcode(db, _settings(tmp_path), media_file)

    assert len(payload.attachments) == 1
    assert payload.attachments[0].stream_index == 4
    assert payload.attachments[0].filename == "Poster Font.ttf"
    assert payload.attachments[0].mimetype == "application/x-truetype-font"


def test_validation_maps_sidecars_cover_attachments_metadata_and_dispositions(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        media_file.has_embedded_cover = True
        media_file.embedded_cover_stream_index = 3
        db.commit()
        sidecar = media_file.external_subtitles[0]
        plan = _compatibility_plan()
        plan.container = "mkv"
        plan.external_subtitles = [
            ExternalSubtitlePlan(subtitle_id=sidecar.id, action="encode", codec="srt", language="en")
        ]
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan)

    assert validation.valid is True
    assert validation.ffmpeg_arguments.count("-i") == 2
    assert "1:0" in validation.ffmpeg_arguments
    assert "0:t?" in validation.ffmpeg_arguments
    assert "0:3" in validation.ffmpeg_arguments
    assert "attached_pic" in validation.ffmpeg_arguments
    assert "-map_metadata" in validation.ffmpeg_arguments
    assert "-map_chapters" in validation.ffmpeg_arguments
    assert "-disposition:a:0" in validation.ffmpeg_arguments


def test_validation_blocks_container_codec_and_escaping_source(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        plan = _compatibility_plan()
        plan.container = "webm"
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan)
        assert validation.valid is False
        assert any("not supported" in error for error in validation.errors)

        media_file.relative_path = "../outside.mkv"
        try:
            transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan)
        except ValueError as exc:
            assert "escapes" in str(exc)
        else:
            raise AssertionError("Escaping source path was accepted")


def test_validation_refuses_existing_target_and_unavailable_hardware(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        plan = _compatibility_plan()
        plan.video_streams[0].encoder = "h264_nvenc"
        output = Path(transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan).output_path)
        output.write_bytes(b"do-not-overwrite")
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan)

    assert validation.valid is False
    assert any("already exists" in error for error in validation.errors)
    assert any("failed its capability test" in error for error in validation.errors)
    assert output.read_bytes() == b"do-not-overwrite"


def test_capabilities_detect_and_smoke_test_dynamic_hardware_encoders(monkeypatch, tmp_path) -> None:
    def fake_run(arguments, **_kwargs):
        if "-version" in arguments:
            return SimpleNamespace(returncode=0, stdout="ffmpeg version test\n", stderr="")
        if "-encoders" in arguments:
            return SimpleNamespace(
                returncode=0,
                stdout=" V..... libx264 H.264\n V..... h264_v4l2m2m V4L2 mem2mem H.264 encoder\n",
                stderr="",
            )
        if "-muxers" in arguments:
            return SimpleNamespace(
                returncode=0,
                stdout=" E  matroska Matroska\n E  mp4 MP4\n",
                stderr="",
            )
        if "-h" in arguments:
            return SimpleNamespace(
                returncode=0,
                stdout="  -preset <string> encoder preset\n  -cq <float> constant quality\n",
                stderr="",
            )
        if "h264_v4l2m2m" in arguments:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(arguments)

    monkeypatch.setattr(transcoding.subprocess, "run", fake_run)
    capabilities = transcoding.get_transcode_capabilities(_settings(tmp_path), refresh=True)
    by_name = {encoder.name: encoder for encoder in capabilities.encoders}
    assert by_name["libx264"].hardware is False
    assert by_name["h264_v4l2m2m"].hardware is True
    assert by_name["h264_v4l2m2m"].tested is True
    assert by_name["h264_v4l2m2m"].available is True
    assert by_name["h264_v4l2m2m"].options == ["cq", "preset"]
    assert capabilities.dolby_vision_passthrough is True


def test_intel_vaapi_probe_uses_drm_render_node_and_upload(monkeypatch, tmp_path) -> None:
    render_node = tmp_path / "renderD128"
    render_node.touch()
    calls = []

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transcoding, "_is_linux", lambda: True)
    monkeypatch.setattr(transcoding.subprocess, "run", fake_run)

    available, error = transcoding._test_hardware_encoder("ffmpeg-test", "h264_vaapi", str(render_node))

    assert available is True
    assert error is None
    assert calls == [
        [
            "ffmpeg-test",
            "-hide_banner",
            "-loglevel",
            "error",
            "-init_hw_device",
            f"vaapi=va:{render_node}",
            "-filter_hw_device",
            "va",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.1",
            "-vf",
            "format=nv12,hwupload",
            "-frames:v",
            "1",
            "-c:v",
            "h264_vaapi",
            "-f",
            "null",
            "-",
        ]
    ]


def test_intel_qsv_probe_derives_from_vaapi_render_node(monkeypatch, tmp_path) -> None:
    render_node = tmp_path / "renderD128"
    render_node.touch()
    calls = []

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transcoding, "_is_linux", lambda: True)
    monkeypatch.setattr(transcoding.subprocess, "run", fake_run)

    available, error = transcoding._test_hardware_encoder("ffmpeg-test", "av1_qsv", str(render_node))

    assert available is True
    assert error is None
    assert "qsv=qs@va" in calls[0]
    assert "-filter_hw_device" in calls[0]
    assert "format=nv12,hwupload=derive_device=qsv" in calls[0]


def test_intel_hardware_plan_initializes_device_and_uploads_frames(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    render_node = tmp_path / "renderD128"
    render_node.touch()
    capabilities = _capabilities()
    capabilities.encoders.append(
        TranscodeEncoderCapability(
            name="h264_vaapi",
            codec="h264",
            hardware=True,
            tested=True,
            available=True,
        )
    )
    capabilities.encoders.append(
        TranscodeEncoderCapability(
            name="h264_qsv",
            codec="h264",
            hardware=True,
            tested=True,
            available=True,
        )
    )
    monkeypatch.setattr(transcoding, "_is_linux", lambda: True)
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: capabilities)
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        plan = _compatibility_plan()
        plan.video_streams[0].encoder = "h264_vaapi"
        settings = _settings(tmp_path)
        settings.hardware_render_node = str(render_node)
        validation = transcoding.validate_transcode_plan(db, settings, media_file, plan)
        plan.video_streams[0].encoder = "h264_qsv"
        validation_qsv = transcoding.validate_transcode_plan(db, settings, media_file, plan)

    assert validation.valid is True
    assert "-init_hw_device" in validation.ffmpeg_arguments
    assert f"vaapi=va:{render_node}" in validation.ffmpeg_arguments
    assert "-filter_hw_device" in validation.ffmpeg_arguments
    filter_index = validation.ffmpeg_arguments.index("-filter:v:0")
    assert validation.ffmpeg_arguments[filter_index + 1].endswith("format=nv12,hwupload")
    assert "-pix_fmt:v:0" not in validation.ffmpeg_arguments
    assert "-qp:v:0" in validation.ffmpeg_arguments
    assert "-crf:v:0" not in validation.ffmpeg_arguments
    assert "-preset:v:0" not in validation.ffmpeg_arguments
    assert "qsv=qs@va" in validation_qsv.ffmpeg_arguments
    assert "-global_quality:v:0" in validation_qsv.ffmpeg_arguments
    assert "-crf:v:0" not in validation_qsv.ffmpeg_arguments
    qsv_filter_index = validation_qsv.ffmpeg_arguments.index("-filter:v:0")
    assert validation_qsv.ffmpeg_arguments[qsv_filter_index + 1].endswith("format=nv12,hwupload=derive_device=qsv")


def test_dolby_vision_generation_is_not_pretended(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        plan = _compatibility_plan()
        plan.dynamic_range = "dolby_vision"
        validation = transcoding.validate_transcode_plan(db, _settings(tmp_path), media_file, plan)

    assert validation.valid is False
    assert any("detected Dolby Vision source" in error for error in validation.errors)
    assert any("does not synthesize Dolby Vision" in error for error in validation.errors)


def test_successful_execution_publishes_variant_without_touching_source(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "SessionLocal", factory)
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())

    class FakePipe:
        def __init__(self, lines=None, text="") -> None:
            self.lines = lines or []
            self.text = text

        def __iter__(self):
            return iter(self.lines)

        def read(self):
            return self.text

    class FakeProcess:
        def __init__(self, arguments, **kwargs) -> None:
            assert kwargs["shell"] is False
            Path(arguments[-1]).write_bytes(b"transcoded-result")
            self.stdout = FakePipe(["out_time_us=60000000\n", "speed=2.0x\n", "progress=end\n"])
            self.stderr = FakePipe(text="")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(transcoding.subprocess, "Popen", FakeProcess)

    with factory() as db:
        media_file = _media_file(db, tmp_path)
        source_path = Path(media_file.library_root.path) / media_file.relative_path
        source_before = source_path.read_bytes()
        job, _validation = transcoding.queue_transcode_job(db, _settings(tmp_path), media_file, _compatibility_plan())
        job_id = job.id
        output_path = Path(job.output_path_snapshot)

    library_id = transcoding.execute_transcode_job(job_id, is_cancel_requested=lambda _job_id: False)

    with factory() as db:
        completed = db.get(TranscodeJob, job_id)
        variant = db.scalar(select(TranscodeVariant).where(TranscodeVariant.job_id == job_id))
        assert completed is not None
        assert completed.status == JobStatus.completed
        assert completed.progress_percent == 100
        assert variant is not None
        assert variant.analysis_status == "awaiting_analysis"
        analyzed_output = MediaFile(
            library_id=completed.library_id,
            library_root_id=variant.library_root_id,
            relative_path=completed.output_relative_path,
            filename=Path(completed.output_relative_path).name,
            extension="mp4",
            size_bytes=output_path.stat().st_size,
            mtime=output_path.stat().st_mtime,
            scan_status=ScanStatus.ready,
        )
        db.add(analyzed_output)
        db.commit()
        assert transcoding.reconcile_transcode_variants(db, completed.library_id) == 1
        db.refresh(variant)
        db.refresh(completed)
        assert variant.output_file_id == analyzed_output.id
        assert variant.analysis_status == "ready"
        assert completed.result_file_id == analyzed_output.id
    assert library_id > 0
    assert source_path.read_bytes() == source_before
    assert output_path.read_bytes() == b"transcoded-result"


def test_startup_recovery_cancels_job_and_removes_temporary_output(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        job, _validation = transcoding.queue_transcode_job(db, _settings(tmp_path), media_file, _compatibility_plan())
        temporary_path = Path(job.temporary_path)
        temporary_path.write_bytes(b"partial")
        recovered = transcoding.recover_orphaned_transcode_jobs(db)
        db.refresh(job)
        assert recovered == 1
        assert job.status == JobStatus.canceled
    assert not temporary_path.exists()


def test_canceling_running_job_removes_partial_output(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "SessionLocal", factory)
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())

    class FakePipe:
        def __iter__(self):
            return iter(["out_time_us=1000000\n"])

        def read(self):
            return ""

    class FakeProcess:
        def __init__(self, arguments, **_kwargs) -> None:
            Path(arguments[-1]).write_bytes(b"partial")
            self.stdout = FakePipe()
            self.stderr = FakePipe()
            self.returncode = None

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode or 0

    monkeypatch.setattr(transcoding.subprocess, "Popen", FakeProcess)
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        job, _ = transcoding.queue_transcode_job(db, _settings(tmp_path), media_file, _compatibility_plan())
        job_id = job.id
        output_path = Path(job.output_path_snapshot)
        temporary_path = Path(job.temporary_path)

    transcoding.execute_transcode_job(job_id, is_cancel_requested=lambda _job_id: True)
    with factory() as db:
        canceled = db.get(TranscodeJob, job_id)
        assert canceled is not None
        assert canceled.status == JobStatus.canceled
    assert not temporary_path.exists()
    assert not output_path.exists()


def test_transcode_trigger_queues_follow_up_when_scan_is_already_running(tmp_path) -> None:
    factory = _session_factory()
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        running = ScanJob(
            library_id=media_file.library_id,
            status=JobStatus.running,
            job_type="incremental",
            trigger_source=ScanTriggerSource.manual,
            scan_summary={},
        )
        db.add(running)
        db.commit()
        follow_up, created = queue_scan_job(
            db,
            media_file.library_id,
            "incremental",
            trigger_source=ScanTriggerSource.transcode,
            trigger_details={"transcode_job_id": 44},
        )

        assert created is True
        assert follow_up.id != running.id
        assert follow_up.status == JobStatus.queued
        assert follow_up.trigger_source == ScanTriggerSource.transcode


def test_retention_removes_only_terminal_job_and_preserves_variant_and_media(monkeypatch, tmp_path) -> None:
    factory = _session_factory()
    monkeypatch.setattr(transcoding, "get_transcode_capabilities", lambda *_args, **_kwargs: _capabilities())
    with factory() as db:
        media_file = _media_file(db, tmp_path)
        job, _ = transcoding.queue_transcode_job(db, _settings(tmp_path), media_file, _compatibility_plan())
        job.status = JobStatus.completed
        job.finished_at = utc_now() - timedelta(days=100)
        variant = TranscodeVariant(
            group_id=job.group_id,
            job_id=job.id,
            original_file_id=media_file.id,
            library_root_id=media_file.library_root_id,
            output_relative_path="Movie variant.mp4",
            output_filename="Movie variant.mp4",
            source_path_snapshot=job.source_path_snapshot,
            output_path_snapshot=str(Path(media_file.library_root.path) / "Movie variant.mp4"),
            analysis_status="ready",
            output_file_id=media_file.id,
        )
        db.add(variant)
        db.commit()
        variant_id = variant.id
        media_file_id = media_file.id

        assert _prune_transcode_history(db, days=90, storage_limit_bytes=0) == 1
        assert db.get(TranscodeJob, job.id) is None
        retained_variant = db.get(TranscodeVariant, variant_id)
        assert retained_variant is not None
        assert retained_variant.job_id is None
        assert retained_variant.source_path_snapshot == job.source_path_snapshot
        assert retained_variant.output_path_snapshot.endswith("Movie variant.mp4")
        assert db.get(MediaFile, media_file_id) is not None
