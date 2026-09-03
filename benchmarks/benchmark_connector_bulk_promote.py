"""Benchmark provider-neutral staging and promotion with a 100k-item catalog."""

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
    ConnectorConnection,
    ConnectorItem,
    ConnectorSyncStageLibrary,
)
from backend.app.services.connector_contract import RemoteItem  # noqa: E402
from backend.app.services.connector_sync import (  # noqa: E402
    _stage_item_row,
    _upsert_stage_items,
    promote_connector_staging,
)
from backend.app.utils.time import utc_now  # noqa: E402


def run_benchmark(item_count: int, batch_size: int) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory(prefix="medialyze-connector-benchmark-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'benchmark.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        run_id = "connector-benchmark-100k"
        with factory() as db:
            connection = ConnectorConnection(provider="jellyfin", name="Benchmark")
            db.add(connection)
            db.commit()
            db.add(
                ConnectorSyncStageLibrary(
                    sync_run_id=run_id,
                    connection_id=connection.id,
                    remote_id="movies",
                    name="Movies",
                    media_type="movies",
                    last_synced_at=utc_now(),
                )
            )
            db.commit()

            stage_started = perf_counter()
            for offset in range(0, item_count, batch_size):
                upper = min(offset + batch_size, item_count)
                rows = [
                    _stage_item_row(
                        run_id,
                        connection.id,
                        RemoteItem(
                            remote_id=f"item-{index}",
                            library_remote_id="movies",
                            item_type="Movie",
                            remote_path=f"/media/movies/Movie-{index}.mkv",
                            title=f"Movie {index}",
                            size_bytes=index + 1,
                            duration_seconds=60.0,
                        ),
                    )
                    for index in range(offset, upper)
                ]
                _upsert_stage_items(db, rows)
                db.commit()
            stage_seconds = perf_counter() - stage_started

            promote_started = perf_counter()
            promote_connector_staging(db, run_id, connection.id)
            promote_seconds = perf_counter() - promote_started
            visible_items = int(
                db.scalar(select(func.count()).select_from(ConnectorItem)) or 0
            )
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
