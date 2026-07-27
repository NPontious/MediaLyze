"""Benchmark Jellyfin's native staging UPSERT and atomic promote with 100k+ items.

Run from the repository root:
    .venv/bin/python benchmarks/benchmark_jellyfin_bulk_promote.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.db.base import Base  # noqa: E402
from backend.app.models.entities import (  # noqa: E402
    JellyfinItem,
    JellyfinSyncStageItem,
    JellyfinSyncStageLibrary,
)
from backend.app.services.jellyfin_staging import (  # noqa: E402
    cleanup_staging,
    commit_stage_page,
    promote_staging,
)
from backend.app.utils.time import utc_now  # noqa: E402


def run_benchmark(item_count: int, batch_size: int) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory(prefix="medialyze-jellyfin-benchmark-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'benchmark.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        run_id = "benchmark-100k"
        now = utc_now()
        with factory() as db:
            commit_stage_page(
                db,
                JellyfinSyncStageLibrary,
                [{
                    "sync_run_id": run_id,
                    "remote_item_id": "movies",
                    "name": "Movies",
                    "collection_type": "movies",
                    "locations": ["/media/movies"],
                    "mapped_locations": [],
                    "mapped_status": "path_unmapped",
                    "linked_library_id": None,
                    "link_method": None,
                    "last_synced_at": now,
                }],
                conflict_columns=("sync_run_id", "remote_item_id"),
            )

            stage_started = perf_counter()
            for offset in range(0, item_count, batch_size):
                upper = min(offset + batch_size, item_count)
                rows = [{
                    "sync_run_id": run_id,
                    "jellyfin_item_id": f"item-{index}",
                    "library_remote_item_id": "movies",
                    "library_name": "Movies",
                    "item_type": "Movie",
                    "path": f"/media/movies/Movie-{index}.mkv",
                    "title": f"Movie {index}",
                    "provider_ids": {},
                    "image_tags": {},
                    "backdrop_image_tags": [],
                    "raw_limited_payload": {"Size": index + 1, "RunTimeTicks": 600_000_000},
                    "size_bytes": index + 1,
                    "duration_seconds": 60.0,
                    "last_synced_at": now,
                } for index in range(offset, upper)]
                commit_stage_page(
                    db,
                    JellyfinSyncStageItem,
                    rows,
                    conflict_columns=("sync_run_id", "jellyfin_item_id"),
                )
            stage_seconds = perf_counter() - stage_started

            promote_started = perf_counter()
            promote_staging(db, run_id)
            promote_seconds = perf_counter() - promote_started
            visible_items = int(db.scalar(select(func.count()).select_from(JellyfinItem)) or 0)
            cleanup_staging(db, run_id)
        engine.dispose()

    return {
        "items": item_count,
        "batch_size": batch_size,
        "visible_items": visible_items,
        "stage_seconds": round(stage_seconds, 3),
        "stage_items_per_second": round(item_count / stage_seconds, 1),
        "promote_seconds": round(promote_seconds, 3),
        "total_seconds": round(stage_seconds + promote_seconds, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.items < 1 or args.batch_size < 1:
        parser.error("--items and --batch-size must be positive")
    print(json.dumps(run_benchmark(args.items, args.batch_size), indent=2))


if __name__ == "__main__":
    main()
