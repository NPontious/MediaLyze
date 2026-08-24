"""Benchmark the complete provider-neutral path matcher with a 100k catalog."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import sessionmaker

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.db.base import Base  # noqa: E402
from backend.app.models.entities import (  # noqa: E402
    ConnectorConnection,
    ConnectorItem,
    ConnectorLibrary,
    ConnectorLibraryLocation,
    ConnectorMediaMatch,
    ConnectorRootBinding,
    Library,
    LibraryRoot,
    LibraryType,
    MediaFile,
    ScanMode,
    ScanStatus,
)
from backend.app.services.connector_matching import recompute_connector_matches  # noqa: E402


def run_benchmark(item_count: int, batch_size: int) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory(prefix="medialyze-connector-matcher-") as directory:
        root_path = Path(directory) / "media"
        root_path.mkdir()
        engine = create_engine(f"sqlite:///{Path(directory) / 'benchmark.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with factory() as db:
            library = Library(
                name="Movies",
                path=str(root_path),
                type=LibraryType.movies,
                scan_mode=ScanMode.manual,
                scan_config={},
            )
            connection = ConnectorConnection(provider="jellyfin", name="Benchmark")
            db.add_all([library, connection])
            db.flush()
            root = LibraryRoot(
                library_id=library.id,
                path=str(root_path),
                display_name="Primary",
                path_key=str(root_path).casefold(),
            )
            remote_library = ConnectorLibrary(
                connection_id=connection.id,
                remote_id="movies",
                name="Movies",
            )
            db.add_all([root, remote_library])
            db.flush()
            location = ConnectorLibraryLocation(
                connector_library_id=remote_library.id,
                remote_path="/remote/movies",
                normalized_path="/remote/movies",
            )
            db.add(location)
            db.flush()
            db.add(
                ConnectorRootBinding(
                    location_id=location.id,
                    library_root_id=root.id,
                    source_prefix="/remote/movies",
                    normalized_source_prefix="/remote/movies",
                )
            )
            db.commit()

            seed_started = perf_counter()
            for offset in range(0, item_count, batch_size):
                upper = min(offset + batch_size, item_count)
                db.execute(
                    insert(MediaFile),
                    [
                        {
                            "library_id": library.id,
                            "library_root_id": root.id,
                            "relative_path": f"Movie-{index}.mkv",
                            "filename": f"Movie-{index}.mkv",
                            "extension": "mkv",
                            "size_bytes": index + 1,
                            "mtime": 1.0,
                            "scan_status": ScanStatus.ready,
                        }
                        for index in range(offset, upper)
                    ],
                )
                db.execute(
                    insert(ConnectorItem),
                    [
                        {
                            "connection_id": connection.id,
                            "connector_library_id": remote_library.id,
                            "remote_id": f"item-{index}",
                            "item_type": "Movie",
                            "remote_path": f"/remote/movies/Movie-{index}.mkv",
                            "normalized_remote_path": f"/remote/movies/Movie-{index}.mkv",
                            "title": f"Movie {index}",
                            "size_bytes": index + 1,
                        }
                        for index in range(offset, upper)
                    ],
                )
                db.commit()
            seed_seconds = perf_counter() - seed_started

            match_started = perf_counter()
            summary = recompute_connector_matches(db, connection_id=connection.id)
            match_seconds = perf_counter() - match_started
            match_count = int(
                db.scalar(select(func.count()).select_from(ConnectorMediaMatch)) or 0
            )
        engine.dispose()

    return {
        "items": item_count,
        "batch_size": batch_size,
        "matches": match_count,
        "matched_summary": int(summary.get("matched", 0)),
        "seed_seconds": round(seed_seconds, 3),
        "match_seconds": round(match_seconds, 3),
        "match_items_per_second": round(item_count / match_seconds, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    args = parser.parse_args()
    if args.items < 1 or args.batch_size < 1:
        parser.error("--items and --batch-size must be positive")
    print(json.dumps(run_benchmark(args.items, args.batch_size), indent=2))


if __name__ == "__main__":
    main()
