"""Benchmark cold and cached library-statistics calculations on synthetic data."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from _support import batch_ranges, create_benchmark_database, environment_metadata, measure_repeated
from sqlalchemy import insert

from backend.app.models.entities import (
    AudioStream,
    Library,
    LibraryType,
    MediaContentCategory,
    MediaFile,
    MediaFormat,
    ScanMode,
    ScanStatus,
    SubtitleStream,
    VideoStream,
)
from backend.app.services.library_service import get_library_statistics
from backend.app.services.stats_cache import stats_cache

DEFAULT_PANELS = (
    "container,video_codec,resolution,hdr_type,video_bit_depth,audio_codecs,"
    "audio_spatial_profiles,audio_languages,audio_channels,sample_rates,"
    "subtitle_languages,subtitle_codecs,subtitle_sources,quality_score,"
    "duration,size,bitrate,audio_bitrate,chapter_counts"
)


def _seed_catalog(db, library_id: int, item_count: int, batch_size: int) -> None:
    analyzed_at = datetime.now(UTC)
    for start, end in batch_ranges(item_count, batch_size):
        media_rows = []
        format_rows = []
        video_rows = []
        audio_rows = []
        subtitle_rows = []
        for index in range(start, end):
            media_file_id = index + 1
            width, height = (1920, 1080) if index % 3 else (3840, 2160)
            codec = "hevc" if index % 2 else "h264"
            audio_codec = "eac3" if index % 3 else "aac"
            audio_language = "de" if index % 4 == 0 else "en"
            hdr_type = "HDR10" if index % 4 == 0 else "SDR"
            duration = 1_800.0 + index % 10_800
            size_bytes = 300_000_000 + (index % 20) * 100_000_000
            bitrate = 2_000_000 + index % 10_000_000
            audio_bitrate = 192_000 + (index % 4) * 96_000
            media_rows.append(
                {
                    "id": media_file_id,
                    "library_id": library_id,
                    "relative_path": f"collection-{index // 1000:04}/Movie-{index:08}.mkv",
                    "filename": f"Movie-{index:08}.mkv",
                    "extension": "mkv",
                    "size_bytes": size_bytes,
                    "mtime": 1_700_000_000.0 + index,
                    "last_analyzed_at": analyzed_at,
                    "scan_status": ScanStatus.ready,
                    "quality_score": index % 10 + 1,
                    "quality_score_raw": float(index % 10 + 1),
                    "quality_score_breakdown": {},
                    "duration_seconds": duration,
                    "bitrate": bitrate,
                    "audio_bitrate": audio_bitrate,
                    "primary_video_codec": codec,
                    "primary_video_width": width,
                    "primary_video_height": height,
                    "primary_video_resolution_pixels": width * height,
                    "primary_video_hdr_type": hdr_type,
                    "max_audio_bit_depth": 24 if index % 2 else 16,
                    "min_audio_codec": audio_codec,
                    "min_audio_language": audio_language,
                    "min_subtitle_codec": "subrip",
                    "min_subtitle_language": "en",
                    "chapter_count": index % 18,
                    "has_internal_subtitles": True,
                    "audio_codecs_search": audio_codec,
                    "audio_spatial_profiles_search": "atmos" if index % 5 == 0 else "",
                    "audio_languages_search": audio_language,
                    "subtitle_languages_search": "en",
                    "subtitle_codecs_search": "subrip",
                    "subtitle_sources_search": "internal",
                    "content_category": MediaContentCategory.main,
                }
            )
            format_rows.append(
                {
                    "media_file_id": media_file_id,
                    "container_format": "matroska",
                    "duration": duration,
                    "bit_rate": bitrate,
                    "probe_score": 100,
                }
            )
            video_rows.append(
                {
                    "media_file_id": media_file_id,
                    "stream_index": 0,
                    "codec": codec,
                    "width": width,
                    "height": height,
                    "bit_depth": 10 if hdr_type != "SDR" else 8,
                    "hdr_type": hdr_type,
                }
            )
            audio_rows.append(
                {
                    "media_file_id": media_file_id,
                    "stream_index": 1,
                    "codec": audio_codec,
                    "channels": 6 if index % 3 else 2,
                    "channel_layout": "5.1" if index % 3 else "stereo",
                    "sample_rate": 48_000,
                    "bit_rate": audio_bitrate,
                    "bit_depth": 24 if index % 2 else 16,
                    "language": audio_language,
                    "title": f"Track {index % 5}",
                    "artist": f"Artist {index % 20}",
                    "album": f"Album {index % 50}",
                    "genre": f"Genre {index % 8}",
                    "date": str(2000 + index % 25),
                }
            )
            subtitle_rows.append(
                {
                    "media_file_id": media_file_id,
                    "stream_index": 2,
                    "codec": "subrip",
                    "language": "en",
                    "subtitle_type": "text",
                }
            )

        db.execute(insert(MediaFile), media_rows)
        db.execute(insert(MediaFormat), format_rows)
        db.execute(insert(VideoStream), video_rows)
        db.execute(insert(AudioStream), audio_rows)
        db.execute(insert(SubtitleStream), subtitle_rows)
        db.commit()


def run_benchmark(item_count: int, batch_size: int, repeats: int, panel_names: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="medialyze-statistics-benchmark-") as directory:
        engine, factory = create_benchmark_database(Path(directory) / "benchmark.sqlite3")
        with factory() as db:
            library = Library(
                name="Statistics benchmark",
                path=str(Path(directory) / "media"),
                type=LibraryType.movies,
                scan_mode=ScanMode.manual,
                scan_config={},
            )
            db.add(library)
            db.commit()
            _seed_catalog(db, library.id, item_count, batch_size)

            cache_key = str(id(db.get_bind()))
            panel_filter = list(panel_names)

            def cold_query():
                stats_cache.invalidate(cache_key, library.id)
                return query_statistics(db, library.id, panel_filter)

            cold_timing, result_shape = measure_repeated(cold_query, repeats)
            warm_timing, warm_shape = measure_repeated(
                lambda: query_statistics(db, library.id, panel_filter),
                repeats,
            )
            if warm_shape != result_shape:
                raise RuntimeError("Cold and warm statistics results differ")

        engine.dispose()

    return {
        "benchmark": "library_statistics",
        "items": item_count,
        "batch_size": batch_size,
        "repeats": repeats,
        "panels": panel_names,
        "application_cache_miss": cold_timing,
        "application_cache_hit": warm_timing,
        "result_shape": result_shape,
        "environment": environment_metadata(),
    }


def query_statistics(db, library_id: int, panel_filter: list[str]) -> dict:
    statistics = get_library_statistics(db, library_id, requested_panels=panel_filter)
    if statistics is None:
        raise RuntimeError("Library statistics were not returned")
    return {
        "container_categories": len(statistics.container_distribution),
        "video_codec_categories": len(statistics.video_codec_distribution),
        "audio_language_categories": len(statistics.audio_language_distribution),
        "numeric_distributions": len(statistics.numeric_distributions),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--panels", default=DEFAULT_PANELS)
    args = parser.parse_args()
    if min(args.items, args.batch_size, args.repeats) < 1:
        parser.error("--items, --batch-size, and --repeats must be positive")
    panel_names = [panel.strip() for panel in args.panels.split(",") if panel.strip()]
    if not panel_names:
        parser.error("--panels must contain at least one panel ID")
    print(
        json.dumps(run_benchmark(args.items, args.batch_size, args.repeats, panel_names), indent=2)
    )


if __name__ == "__main__":
    main()
