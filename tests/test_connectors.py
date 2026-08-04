from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.db.base import Base
from backend.app.db.session import init_db
from backend.app.models.entities import (
    ConnectorConnection,
    ConnectorCredential,
    ConnectorItem,
    ConnectorLibrary,
    ConnectorLibraryLocation,
    ConnectorMediaMatch,
    ConnectorRootBinding,
    ConnectorSyncJob,
    ConnectorSyncStageItem,
    ConnectorSyncStageLibrary,
    JellyfinConnection,
    JellyfinItem,
    JellyfinLibrary,
    JellyfinMediaMatch,
    JellyfinPathMapping,
    JobStatus,
    Library,
    LibraryRoot,
    LibraryType,
    MediaFile,
    ScanMode,
    ScanStatus,
)
from backend.app.schemas.connectors import (
    ConnectorBindingBatchUpdate,
    ConnectorBindingWrite,
    ConnectorConnectionCreate,
    ConnectorLibraryLinkBatchUpdate,
    ConnectorLibraryLinkWrite,
)
from backend.app.services.connector_contract import RemoteItem
from backend.app.services.connector_matching import recompute_connector_matches
from backend.app.services.connector_pathing import normalize_connector_path, normalize_target_subpath
from backend.app.services.connector_service import (
    create_connector_connection,
    remove_manual_connector_match,
    replace_connector_bindings,
    replace_connector_library_links,
    restore_automatic_connector_match,
    serialize_connector_connection,
    set_manual_connector_match,
)
from backend.app.services.connector_sync import (
    create_or_get_connector_sync_job,
    claim_connector_sync_job,
    _stage_item_row,
    _upsert_stage_items,
    promote_connector_staging,
    recover_orphaned_connector_sync_jobs,
    request_connector_sync_cancellation,
    run_connector_recompute,
)
from backend.app.services.connector_security import redact_connector_error
from backend.app.utils.time import utc_now


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _connector_graph(db: Session, root_path: str):
    library = Library(
        name="Movies",
        path=root_path,
        type=LibraryType.movies,
        scan_mode=ScanMode.manual,
        scan_config={},
    )
    connection = ConnectorConnection(
        provider="jellyfin",
        name="Living Room",
        base_url="http://jellyfin.local",
        enabled=True,
    )
    db.add_all([library, connection])
    db.flush()
    root = LibraryRoot(
        library_id=library.id,
        path=root_path,
        display_name="Primary",
        path_key=root_path.casefold(),
    )
    remote_library = ConnectorLibrary(
        connection_id=connection.id,
        remote_id="remote-library",
        name="Movies",
    )
    db.add_all([root, remote_library])
    db.flush()
    location = ConnectorLibraryLocation(
        connector_library_id=remote_library.id,
        remote_path="/srv/media",
        normalized_path="/srv/media",
    )
    db.add(location)
    db.flush()
    return connection, library, root, remote_library, location


def test_connector_path_normalization_handles_windows_unc_and_rejects_escape() -> None:
    assert normalize_connector_path(r"C:\Media\Movies\Film.mkv").display == "C:/Media/Movies/Film.mkv"
    assert normalize_connector_path(r"\\nas\Media\Film.mkv").display == "//nas/Media/Film.mkv"
    assert normalize_connector_path("/Media/Film.mkv", case_mode="insensitive").key == "/media/film.mkv"
    with pytest.raises(ValueError, match="relative"):
        normalize_target_subpath("../escape")


def test_connector_matcher_allows_multiple_items_for_one_file_and_prefers_longest_prefix(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, library, root, remote_library, location = _connector_graph(db, str(tmp_path))
        media = MediaFile(
            library_id=library.id,
            library_root_id=root.id,
            relative_path="nested/movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=123,
            mtime=1.0,
            scan_status=ScanStatus.ready,
        )
        db.add_all(
            [
                ConnectorRootBinding(
                    location_id=location.id,
                    library_root_id=root.id,
                    source_prefix="/srv",
                    normalized_source_prefix="/srv",
                    target_subpath="wrong",
                    case_mode="sensitive",
                    priority=0,
                ),
                ConnectorRootBinding(
                    location_id=location.id,
                    library_root_id=root.id,
                    source_prefix="/srv/media",
                    normalized_source_prefix="/srv/media",
                    target_subpath="nested",
                    case_mode="sensitive",
                    priority=0,
                ),
                ConnectorItem(
                    connection_id=connection.id,
                    connector_library_id=remote_library.id,
                    remote_id="item-a",
                    item_type="Movie",
                    remote_path="/srv/media/movie.mkv",
                    normalized_remote_path="/srv/media/movie.mkv",
                    title="A",
                ),
                ConnectorItem(
                    connection_id=connection.id,
                    connector_library_id=remote_library.id,
                    remote_id="item-b",
                    item_type="Movie",
                    remote_path="/srv/media/movie.mkv",
                    normalized_remote_path="/srv/media/movie.mkv",
                    title="B",
                ),
                media,
            ]
        )
        db.commit()

        summary = recompute_connector_matches(db, connection_id=connection.id)
        matches = list(db.scalars(select(ConnectorMediaMatch).order_by(ConnectorMediaMatch.id)))

    assert summary["matched"] == 2
    assert len(matches) == 2
    assert {match.media_file_id for match in matches} == {media.id}


def test_manual_connector_match_survives_recompute(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, library, root, remote_library, _location = _connector_graph(db, str(tmp_path))
        media = MediaFile(
            library_id=library.id,
            library_root_id=root.id,
            relative_path="movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=123,
            mtime=1.0,
            scan_status=ScanStatus.ready,
        )
        item = ConnectorItem(
            connection_id=connection.id,
            connector_library_id=remote_library.id,
            remote_id="manual",
            item_type="Movie",
            remote_path="/unmapped/movie.mkv",
            normalized_remote_path="/unmapped/movie.mkv",
            title="Manual",
        )
        db.add_all([media, item])
        db.commit()
        set_manual_connector_match(db, item, media.id)

        summary = recompute_connector_matches(db, connection_id=connection.id)
        match = db.scalar(
            select(ConnectorMediaMatch).where(ConnectorMediaMatch.connector_item_id == item.id)
        )

    assert summary["manual_preserved"] == 1
    assert match is not None
    assert match.match_method == "manual"
    assert match.media_file_id == media.id


def test_binding_batch_rejects_equivalent_active_rules_atomically(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, _library, root, _remote_library, location = _connector_graph(db, str(tmp_path))
        db.commit()
        payload = ConnectorBindingBatchUpdate(
            bindings=[
                ConnectorBindingWrite(
                    location_id=location.id,
                    library_root_id=root.id,
                    source_prefix="/srv/media",
                ),
                ConnectorBindingWrite(
                    location_id=location.id,
                    library_root_id=root.id,
                    source_prefix="/srv/media/",
                ),
            ]
        )
        with pytest.raises(ValueError, match="Equivalent"):
            replace_connector_bindings(db, connection.id, payload)
        count = db.scalar(select(func.count()).select_from(ConnectorRootBinding))

    assert count == 0


def test_connector_sync_is_single_flight_per_connection_but_independent_between_connections() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        first = ConnectorConnection(provider="jellyfin", name="First")
        second = ConnectorConnection(provider="jellyfin", name="Second")
        db.add_all([first, second])
        db.commit()

        first_job, first_accepted = create_or_get_connector_sync_job(db, first.id)
        duplicate_job, duplicate_accepted = create_or_get_connector_sync_job(db, first.id)
        second_job, second_accepted = create_or_get_connector_sync_job(db, second.id)

        assert first_accepted is True
        assert duplicate_accepted is False
        assert duplicate_job.id == first_job.id
        assert second_accepted is True
        assert second_job.connection_id == second.id
        assert db.scalar(select(func.count()).select_from(ConnectorSyncJob)) == 2


def test_connector_startup_recovery_cancels_orphaned_jobs() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection = ConnectorConnection(provider="jellyfin", name="Recovery")
        db.add(connection)
        db.commit()
        job, _accepted = create_or_get_connector_sync_job(db, connection.id)
        job.status = JobStatus.running
        db.commit()

        assert recover_orphaned_connector_sync_jobs(db) == 1
        db.refresh(job)
        assert job.status == JobStatus.canceled
        assert job.active_lock is None
        assert job.finished_at is not None


def test_canceled_queued_connector_job_cannot_be_claimed() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection = ConnectorConnection(provider="jellyfin", name="Canceled")
        db.add(connection)
        db.commit()
        job, _accepted = create_or_get_connector_sync_job(db, connection.id)

        canceled = request_connector_sync_cancellation(db, connection.id, job.id)

        assert canceled is not None
        assert canceled.status == JobStatus.canceled
        assert claim_connector_sync_job(db, job.id) is None


def test_startup_recovery_removes_abandoned_connector_staging_rows() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection = ConnectorConnection(provider="jellyfin", name="Staging recovery")
        db.add(connection)
        db.commit()
        db.add(
            ConnectorSyncStageLibrary(
                sync_run_id="abandoned",
                connection_id=connection.id,
                remote_id="movies",
                name="Movies",
                last_synced_at=utc_now(),
            )
        )
        db.commit()

        assert recover_orphaned_connector_sync_jobs(db) == 0
        assert db.scalar(select(func.count()).select_from(ConnectorSyncStageLibrary)) == 0


def test_connector_secret_is_write_only_in_serialized_contract() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection = create_connector_connection(
            db,
            ConnectorConnectionCreate(
                provider="jellyfin",
                name="Secret",
                base_url="http://jellyfin.local",
                secret="do-not-return",
            ),
        )
        response = serialize_connector_connection(db, connection).model_dump()

        assert response["has_secret"] is True
        assert "secret" not in response
        assert "do-not-return" not in str(response)


def test_connector_config_rejects_secret_fields_and_errors_are_redacted() -> None:
    _engine, factory = _session_factory()
    with factory() as db, pytest.raises(ValueError, match="dedicated secret field"):
        create_connector_connection(
            db,
            ConnectorConnectionCreate(
                provider="jellyfin",
                name="Unsafe",
                config={"nested": {"api_key": "do-not-store"}},
            ),
        )
    error = redact_connector_error(
        RuntimeError("request failed token=do-not-log at http://user:password@example.test"),
        secrets=("do-not-log",),
    )
    assert "do-not-log" not in error
    assert "password" not in error
    assert "***" in error


def test_deleted_media_locator_recomputes_previously_matched_item(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, library, root, remote_library, location = _connector_graph(db, str(tmp_path))
        media = MediaFile(
            library_id=library.id,
            library_root_id=root.id,
            relative_path="movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=123,
            mtime=1.0,
            scan_status=ScanStatus.ready,
        )
        item = ConnectorItem(
            connection_id=connection.id,
            connector_library_id=remote_library.id,
            remote_id="deleted",
            item_type="Movie",
            remote_path="/srv/media/movie.mkv",
            title="Deleted",
        )
        db.add_all([
            ConnectorRootBinding(
                location_id=location.id,
                library_root_id=root.id,
                source_prefix="/srv/media",
                normalized_source_prefix="/srv/media",
            ),
            media,
            item,
        ])
        db.commit()
        recompute_connector_matches(db, connection_id=connection.id)
        assert item.match_status == "matched"

        db.delete(media)
        db.commit()
        recompute_connector_matches(
            db,
            media_file_locators={(root.id, "movie.mkv")},
        )

        assert item.match_status == "no_local_file"
        assert db.scalar(
            select(ConnectorMediaMatch).where(ConnectorMediaMatch.connector_item_id == item.id)
        ) is None


def test_manual_unmatch_is_persistent_until_automatic_matching_is_restored(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, library, root, remote_library, location = _connector_graph(db, str(tmp_path))
        media = MediaFile(
            library_id=library.id,
            library_root_id=root.id,
            relative_path="movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=123,
            mtime=1.0,
            scan_status=ScanStatus.ready,
        )
        item = ConnectorItem(
            connection_id=connection.id,
            connector_library_id=remote_library.id,
            remote_id="ignored",
            item_type="Movie",
            remote_path="/srv/media/movie.mkv",
            title="Ignored",
        )
        db.add_all([
            ConnectorRootBinding(
                location_id=location.id,
                library_root_id=root.id,
                source_prefix="/srv/media",
                normalized_source_prefix="/srv/media",
            ),
            media,
            item,
        ])
        db.commit()
        recompute_connector_matches(db, connection_id=connection.id)

        assert remove_manual_connector_match(db, item) is True
        recompute_connector_matches(db, connection_id=connection.id)
        assert item.match_status == "ignored"

        restore_automatic_connector_match(db, item)
        assert item.match_status == "matched"


def test_manual_library_links_are_many_to_many_and_durable(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, first, _root, remote_library, _location = _connector_graph(db, str(tmp_path))
        second = Library(
            name="Archive",
            path=str(tmp_path / "archive"),
            type=LibraryType.movies,
            scan_mode=ScanMode.manual,
            scan_config={},
        )
        db.add(second)
        db.commit()

        libraries = replace_connector_library_links(
            db,
            connection.id,
            ConnectorLibraryLinkBatchUpdate(
                links=[
                    ConnectorLibraryLinkWrite(
                        connector_library_id=remote_library.id,
                        library_ids=[first.id, second.id],
                    )
                ]
            ),
        )

        assert libraries[0].linked_library_ids == [first.id, second.id]
        assert first.preferred_connector_connection_id == connection.id
        assert second.preferred_connector_connection_id == connection.id


def test_connector_recompute_runs_as_persisted_job(tmp_path) -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection, _library, _root, _remote_library, _location = _connector_graph(db, str(tmp_path))
        db.commit()
        job, accepted = create_or_get_connector_sync_job(
            db,
            connection.id,
            trigger_source="binding",
            job_type="recompute",
        )
        assert accepted is True
        assert claim_connector_sync_job(db, job.id) is not None

        assert run_connector_recompute(db, job.id) == {}
        db.refresh(job)
        assert job.status == JobStatus.completed
        assert job.job_type == "recompute"


def test_connector_promote_can_roll_back_without_replacing_live_snapshot() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection = ConnectorConnection(provider="jellyfin", name="Atomic")
        db.add(connection)
        db.flush()
        live_library = ConnectorLibrary(
            connection_id=connection.id,
            remote_id="movies",
            name="Old catalog",
        )
        db.add(live_library)
        db.flush()
        db.add(
            ConnectorItem(
                connection_id=connection.id,
                connector_library_id=live_library.id,
                remote_id="old-item",
                item_type="Movie",
                title="Old item",
            )
        )
        db.commit()
        run_id = "failed-run"
        now = utc_now()
        db.add_all(
            [
                ConnectorSyncStageLibrary(
                    sync_run_id=run_id,
                    connection_id=connection.id,
                    remote_id="movies",
                    name="New catalog",
                    last_synced_at=now,
                ),
                ConnectorSyncStageItem(
                    sync_run_id=run_id,
                    connection_id=connection.id,
                    remote_id="new-item",
                    library_remote_id="movies",
                    item_type="Movie",
                    title="New item",
                    last_synced_at=now,
                ),
            ]
        )
        db.commit()

        promote_connector_staging(db, run_id, connection.id, commit=False)
        db.rollback()

        assert db.scalar(
            select(ConnectorLibrary.name).where(ConnectorLibrary.connection_id == connection.id)
        ) == "Old catalog"
        assert set(
            db.scalars(select(ConnectorItem.remote_id).where(ConnectorItem.connection_id == connection.id))
        ) == {"old-item"}


def test_connector_bulk_stage_and_promote_preserve_normalized_metadata() -> None:
    _engine, factory = _session_factory()
    with factory() as db:
        connection = ConnectorConnection(provider="jellyfin", name="Bulk")
        db.add(connection)
        db.commit()
        run_id = "bulk-run"
        now = utc_now()
        db.add(
            ConnectorSyncStageLibrary(
                sync_run_id=run_id,
                connection_id=connection.id,
                remote_id="movies",
                name="Movies",
                last_synced_at=now,
            )
        )
        _upsert_stage_items(
            db,
            [
                _stage_item_row(
                    run_id,
                    connection.id,
                    RemoteItem(
                        remote_id="item",
                        library_remote_id="movies",
                        item_type="Movie",
                        remote_path="/media/movie.mkv",
                        title="Movie",
                        original_title="Original",
                        date_created=now,
                        provider_ids={"Imdb": "tt123"},
                        size_bytes=123,
                        duration_seconds=60.5,
                    ),
                )
            ],
        )
        db.commit()

        promote_connector_staging(db, run_id, connection.id)
        item = db.scalar(select(ConnectorItem).where(ConnectorItem.remote_id == "item"))

        assert item is not None
        assert item.connector_library_id is not None
        assert item.original_title == "Original"
        assert item.date_created == now
        assert item.provider_ids == {"Imdb": "tt123"}
        assert item.size_bytes == 123
        assert item.duration_seconds == 60.5


def test_init_db_migrates_legacy_jellyfin_catalog_idempotently(tmp_path) -> None:
    database = tmp_path / "migration.sqlite3"
    engine = create_engine(f"sqlite:///{database}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as db:
        library = Library(
            name="Movies",
            path=str(tmp_path),
            type=LibraryType.movies,
            scan_mode=ScanMode.manual,
            scan_config={},
        )
        legacy_connection = JellyfinConnection(
            id=1,
            base_url="http://jellyfin.local",
            api_key="top-secret",
            enabled=True,
        )
        db.add_all([library, legacy_connection])
        db.flush()
        root = LibraryRoot(
            library_id=library.id,
            path=str(tmp_path),
            display_name="Movies",
            path_key=str(tmp_path).casefold(),
        )
        legacy_library = JellyfinLibrary(
            remote_item_id="jf-library",
            name="Movies",
            locations=["/srv/media"],
            mapped_locations=[str(tmp_path)],
            mapped_status="linked",
            linked_library_id=library.id,
            link_method="manual",
        )
        db.add_all([root, legacy_library])
        db.flush()
        media = MediaFile(
            library_id=library.id,
            library_root_id=root.id,
            relative_path="movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=123,
            mtime=1.0,
            scan_status=ScanStatus.ready,
        )
        item = JellyfinItem(
            jellyfin_item_id="jf-item",
            library_id=legacy_library.id,
            library_name="Movies",
            item_type="Movie",
            path="/srv/media/movie.mkv",
            title="Movie",
            match_status="matched",
        )
        mapping = JellyfinPathMapping(
            jellyfin_path_prefix="/srv/media",
            medialyze_path_prefix=str(tmp_path),
            enabled=True,
        )
        db.add_all([media, item, mapping])
        db.flush()
        db.add(
            JellyfinMediaMatch(
                media_file_id=media.id,
                jellyfin_item_id=item.id,
                match_method="path",
                confidence=1.0,
                status="matched",
            )
        )
        db.commit()

    init_db(engine)
    init_db(engine)

    with factory() as db:
        connection = db.scalar(select(ConnectorConnection).where(ConnectorConnection.provider == "jellyfin"))
        assert connection is not None
        assert db.get(ConnectorCredential, connection.id).secret_payload == "top-secret"
        assert db.scalar(select(func.count()).select_from(ConnectorLibrary)) == 1
        assert db.scalar(select(func.count()).select_from(ConnectorLibraryLocation)) == 1
        assert db.scalar(select(func.count()).select_from(ConnectorItem)) == 1
        assert db.scalar(select(func.count()).select_from(ConnectorMediaMatch)) == 1
        binding = db.scalar(select(ConnectorRootBinding))
        assert binding is not None
        assert binding.case_mode == "insensitive"
        assert binding.library_root_id == root.id
