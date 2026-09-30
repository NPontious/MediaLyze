"""Benchmark filtered and paginated library file queries on a synthetic catalog."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from _support import batch_ranges, create_benchmark_database, environment_metadata, measure_repeated
from sqlalchemy import insert

from backend.app.models.entities import Library, LibraryType, MediaFile, ScanMode, ScanStatus
from backend.app.services.media_search import LibraryFileSearchFilters
from backend.app.services.media_search_index import (
    ensure_media_file_search_index,
    mark_library_search_fields_ready,
)
from backend.app.services.media_service import list_library_files
from backend.app.services.stats_cache import stats_cache


def run_benchmark(item_count: int, batch_size: int, repeats: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="medialyze-file-query-benchmark-") as directory:
        engine, factory = create_benchmark_database(Path(directory) / "benchmark.sqlite3")
        with factory() as db:
            library = Library(
                name="File query benchmark",
                path=str(Path(directory) / "media"),
                type=LibraryType.movies,
                scan_mode=ScanMode.manual,
                scan_config={},
            )
            db.add(library)
            db.commit()

            for start, end in batch_ranges(item_count, batch_size):
                rows = []
                for index in range(start, end):
                    width, height = (1920, 1080) if index % 3 else (3840, 2160)
                    is_rare_match = index % 100 == 0
                    filename = (
                        f"RareBenchmarkNeedle-{index:08}.mkv"
                        if is_rare_match
                        else f"Movie-{index:08}.mkv"
                    )
                    rows.append(
                        {
                            "library_id": library.id,
                            "relative_path": f"collection-{index // 1000:04}/{filename}",
                            "filename": filename,
                            "extension": "mkv",
                            "size_bytes": 500_000_000 + (index % 5) * 250_000_000,
                            "mtime": 1_700_000_000.0 + index,
                            "scan_status": ScanStatus.ready,
                            "quality_score": index % 10 + 1,
                            "duration_seconds": 3_600.0 + index % 7,
                            "bitrate": 5_000_000 + index % 1_000_000,
                            "audio_bitrate": 384_000,
                            "primary_video_codec": "hevc" if index % 2 else "h264",
                            "primary_video_width": width,
                            "primary_video_height": height,
                            "primary_video_resolution_pixels": width * height,
                            "primary_video_hdr_type": "HDR10" if index % 4 == 0 else "SDR",
                            "audio_codecs_search": "eac3 aac" if index % 2 else "aac",
                            "audio_languages_search": "en de" if index % 2 else "en",
                            "subtitle_languages_search": "en",
                            "search_fields_version": 4,
                        }
                    )
                db.execute(insert(MediaFile), rows)
                db.commit()

            with engine.begin() as connection:
                fts_available = ensure_media_file_search_index(connection)
            mark_library_search_fields_ready(db, library.id)

            cache_key = str(id(db.get_bind()))
            cases = {
                "first_page": {"sort_key": "file", "sort_direction": "asc", "offset": 0},
                "deep_page": {
                    "sort_key": "size",
                    "sort_direction": "desc",
                    "offset": max(0, item_count - 50),
                },
                "text_search": {"search": "rarebenchmarkneedle", "sort_key": "file"},
                "metadata_filters": {
                    "search_filters": LibraryFileSearchFilters(
                        search_video_codec="hevc",
                        search_resolution="1920x1080",
                    ),
                    "sort_key": "quality_score",
                    "sort_direction": "desc",
                },
            }
            results = {}
            for name, query in cases.items():

                def run_query(query=query):
                    stats_cache.invalidate(cache_key, library.id)
                    page = list_library_files(
                        db,
                        library.id,
                        limit=50,
                        include_total=True,
                        **query,
                    )
                    return {"returned_rows": len(page.items), "total_matches": page.total}

                timing, outcome = measure_repeated(run_query, repeats)
                results[name] = {**timing, **outcome}

        engine.dispose()

    return {
        "benchmark": "library_file_queries",
        "items": item_count,
        "batch_size": batch_size,
        "repeats": repeats,
        "search_index": "fts5_trigram" if fts_available else "like_fallback",
        "cases": results,
        "environment": environment_metadata(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if min(args.items, args.batch_size, args.repeats) < 1:
        parser.error("--items, --batch-size, and --repeats must be positive")
    print(json.dumps(run_benchmark(args.items, args.batch_size, args.repeats), indent=2))


if __name__ == "__main__":
    main()
