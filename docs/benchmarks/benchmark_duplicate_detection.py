"""Benchmark duplicate signatures, file hashing, and duplicate-group queries."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from _support import batch_ranges, create_benchmark_database, environment_metadata, measure_repeated
from sqlalchemy import insert

from backend.app.models.entities import (
    DuplicateDetectionMode,
    Library,
    LibraryType,
    MediaFile,
    ScanMode,
    ScanStatus,
)
from backend.app.services.duplicates import (
    FILE_HASH_ALGORITHM,
    FileHashDuplicateDetectionStrategy,
    FilenameDuplicateDetectionStrategy,
    list_library_duplicate_groups,
)


def _write_fixture_file(path: Path, group_index: int, file_size_bytes: int) -> None:
    seed = hashlib.sha256(f"duplicate-benchmark-{group_index}".encode()).digest()
    repetitions = (file_size_bytes + len(seed) - 1) // len(seed)
    path.write_bytes((seed * repetitions)[:file_size_bytes])


def _hash_all(
    paths: list[Path], strategy: FileHashDuplicateDetectionStrategy, workers: int
) -> list[dict]:
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(strategy.build_payload, paths))


def run_benchmark(
    item_count: int, batch_size: int, file_size_bytes: int, repeats: int, workers: int
) -> dict:
    setup_started = perf_counter()
    with tempfile.TemporaryDirectory(prefix="medialyze-duplicate-benchmark-") as directory:
        root = Path(directory) / "media"
        file_paths: list[Path] = []
        relative_paths: list[str] = []
        for index in range(item_count):
            group_index = index // 2
            copy_index = index % 2
            filename = f"Movie-{group_index:08}.mkv"
            relative_path = f"copy-{copy_index}/{filename}"
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_fixture_file(path, group_index, file_size_bytes)
            file_paths.append(path)
            relative_paths.append(relative_path)
        fixture_write_seconds = perf_counter() - setup_started

        filename_strategy = FilenameDuplicateDetectionStrategy()
        filename_timing, filename_payloads = measure_repeated(
            lambda: [filename_strategy.build_payload(path) for path in file_paths],
            repeats,
        )
        hash_strategy = FileHashDuplicateDetectionStrategy()
        hash_timing, hash_payloads = measure_repeated(
            lambda: _hash_all(file_paths, hash_strategy, workers),
            repeats,
        )

        engine, factory = create_benchmark_database(Path(directory) / "benchmark.sqlite3")
        with factory() as db:
            library = Library(
                name="Duplicate benchmark",
                path=str(root),
                type=LibraryType.movies,
                scan_mode=ScanMode.manual,
                duplicate_detection_mode=DuplicateDetectionMode.both,
                scan_config={},
            )
            db.add(library)
            db.commit()

            for start, end in batch_ranges(item_count, batch_size):
                rows = []
                for index in range(start, end):
                    filename = file_paths[index].name
                    rows.append(
                        {
                            "library_id": library.id,
                            "relative_path": relative_paths[index],
                            "filename": filename,
                            "extension": "mkv",
                            "size_bytes": file_size_bytes,
                            "mtime": 1_700_000_000.0 + index,
                            "duration_seconds": 3_600.0,
                            "scan_status": ScanStatus.ready,
                            "filename_signature": filename_payloads[index]["filename_signature"],
                            "filename_pattern_signature": filename_payloads[index][
                                "filename_pattern_signature"
                            ],
                            "content_hash": hash_payloads[index]["content_hash"],
                            "content_hash_algorithm": FILE_HASH_ALGORITHM,
                        }
                    )
                db.execute(insert(MediaFile), rows)
                db.commit()

            group_query_results = {}
            for mode in (
                DuplicateDetectionMode.filename,
                DuplicateDetectionMode.filehash,
                DuplicateDetectionMode.both,
            ):
                library.duplicate_detection_mode = mode
                db.commit()
                timing, page = measure_repeated(
                    lambda: list_library_duplicate_groups(db, library.id, limit=50),
                    repeats,
                )
                group_query_results[mode.value] = {
                    **timing,
                    "total_groups": page.total_groups,
                    "duplicate_files": page.duplicate_file_count,
                    "groups_returned": len(page.items),
                }
        engine.dispose()

    hash_bytes_per_second = (
        round(item_count * file_size_bytes / hash_timing["median_seconds"], 1)
        if hash_timing["median_seconds"] > 0
        else None
    )
    return {
        "benchmark": "duplicate_detection",
        "items": item_count,
        "batch_size": batch_size,
        "file_size_bytes": file_size_bytes,
        "total_fixture_bytes": item_count * file_size_bytes,
        "repeats": repeats,
        "hash_workers": workers,
        "fixture_write_seconds": round(fixture_write_seconds, 4),
        "filename_signatures": {
            **filename_timing,
            "files_per_second": round(item_count / filename_timing["median_seconds"], 1)
            if filename_timing["median_seconds"] > 0
            else None,
        },
        "sha256_hashing": {
            **hash_timing,
            "bytes_per_second": hash_bytes_per_second,
        },
        "group_queries": group_query_results,
        "environment": environment_metadata(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=10_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--file-size-bytes", type=int, default=4_096)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if min(args.items, args.batch_size, args.file_size_bytes, args.repeats, args.workers) < 1:
        parser.error(
            "--items, --batch-size, --file-size-bytes, --repeats, and --workers must be positive"
        )
    if args.items < 2:
        parser.error("--items must be at least 2 to create duplicate groups")
    print(
        json.dumps(
            run_benchmark(
                args.items, args.batch_size, args.file_size_bytes, args.repeats, args.workers
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
