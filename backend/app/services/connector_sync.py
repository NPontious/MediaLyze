from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    ConnectorConnection,
    ConnectorItem,
    ConnectorLibrary,
    ConnectorLibraryLocation,
    ConnectorSyncJob,
    ConnectorSyncStageItem,
    ConnectorSyncStageLibrary,
    ConnectorSyncStageLocation,
    JobStatus,
    JellyfinConnection,
    JellyfinItem,
    JellyfinLibrary,
)
from backend.app.db.types import UTCDateTime
from backend.app.services.connector_contract import RemoteItem, RemoteLibrary
from backend.app.services.connector_credentials import read_connector_secret
from backend.app.services.connector_matching import recompute_connector_matches
from backend.app.services.connector_pathing import normalize_connector_path
from backend.app.services.connector_registry import connector_registry
from backend.app.services.connector_security import (
    public_connector_payload,
    redact_connector_error,
)
from backend.app.services.stats_cache import stats_cache
from backend.app.utils.time import utc_now


class ConnectorSyncCancelled(RuntimeError):
    pass


class ConnectorSyncFailed(RuntimeError):
    pass


def create_or_get_connector_sync_job(
    db: Session,
    connection_id: int,
    trigger_source: str = "manual",
    job_type: str = "sync",
) -> tuple[ConnectorSyncJob, bool]:
    active = db.scalar(
        select(ConnectorSyncJob)
        .where(
            ConnectorSyncJob.connection_id == connection_id,
            ConnectorSyncJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .order_by(ConnectorSyncJob.id)
    )
    if active is not None:
        return active, False
    job = ConnectorSyncJob(
        connection_id=connection_id,
        status=JobStatus.queued,
        trigger_source=trigger_source,
        job_type=job_type,
        active_lock=1,
        progress_phase="queued",
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = db.scalar(
            select(ConnectorSyncJob).where(
                ConnectorSyncJob.connection_id == connection_id,
                ConnectorSyncJob.active_lock == 1,
            )
        )
        if active is None:
            raise
        return active, False
    db.refresh(job)
    return job, True


def claim_connector_sync_job(db: Session, job_id: int) -> ConnectorSyncJob | None:
    claimed = db.execute(
        update(ConnectorSyncJob)
        .where(
            ConnectorSyncJob.id == job_id,
            ConnectorSyncJob.status == JobStatus.queued,
            ConnectorSyncJob.active_lock == 1,
            ConnectorSyncJob.cancellation_requested.is_(False),
        )
        .values(
            status=JobStatus.running,
            started_at=utc_now(),
            heartbeat_at=utc_now(),
            progress_phase="starting",
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    return db.get(ConnectorSyncJob, job_id)


def request_connector_sync_cancellation(
    db: Session,
    connection_id: int,
    job_id: int | None = None,
) -> ConnectorSyncJob | None:
    query = select(ConnectorSyncJob).where(
        ConnectorSyncJob.connection_id == connection_id,
        ConnectorSyncJob.status.in_([JobStatus.queued, JobStatus.running]),
    )
    if job_id is not None:
        query = query.where(ConnectorSyncJob.id == job_id)
    job = db.scalar(query.order_by(ConnectorSyncJob.id))
    if job is None:
        return None
    if job.status == JobStatus.queued:
        job.status = JobStatus.canceled
        job.active_lock = None
        job.finished_at = utc_now()
    else:
        job.cancellation_requested = True
    db.commit()
    return job


def recover_orphaned_connector_sync_jobs(db: Session) -> int:
    jobs = list(
        db.scalars(
            select(ConnectorSyncJob).where(
                ConnectorSyncJob.status.in_([JobStatus.queued, JobStatus.running])
            )
        )
    )
    now = utc_now()
    for job in jobs:
        job.status = JobStatus.canceled
        job.active_lock = None
        job.finished_at = now
        job.error = "Canceled during startup recovery"
    if jobs:
        db.flush()
    # No connector workers are active during startup recovery. Any remaining
    # staging rows therefore belong to an interrupted or obsolete run.
    for model in (
        ConnectorSyncStageItem,
        ConnectorSyncStageLocation,
        ConnectorSyncStageLibrary,
    ):
        db.execute(delete(model))
    db.commit()
    return len(jobs)


def _check_cancellation(db: Session, job_id: int) -> None:
    job = db.get(ConnectorSyncJob, job_id)
    if job is None:
        raise ConnectorSyncCancelled("Connector sync job no longer exists")
    db.refresh(job, ["cancellation_requested"])
    if job.cancellation_requested:
        raise ConnectorSyncCancelled("Connector synchronization was canceled")
    job.heartbeat_at = utc_now()
    db.flush()


def _update_progress(
    db: Session,
    job: ConnectorSyncJob,
    phase: str,
    current: int,
    total: int | None = None,
    detail: str | None = None,
) -> None:
    job.progress_phase = phase
    job.progress_current = current
    job.progress_total = total
    job.progress_detail = detail
    job.heartbeat_at = utc_now()
    db.commit()


def _stage_library(
    db: Session,
    run_id: str,
    connection_id: int,
    library: RemoteLibrary,
    *,
    secret: str = "",
) -> None:
    now = utc_now()
    db.merge(
        ConnectorSyncStageLibrary(
            sync_run_id=run_id,
            connection_id=connection_id,
            remote_id=library.remote_id,
            name=library.name,
            media_type=library.media_type,
            provider_payload=public_connector_payload(
                library.provider_payload,
                secrets=(secret,),
            ),
            last_synced_at=now,
        )
    )
    for location in library.locations:
        normalized = normalize_connector_path(location.path).display
        db.merge(
            ConnectorSyncStageLocation(
                sync_run_id=run_id,
                connection_id=connection_id,
                library_remote_id=library.remote_id,
                normalized_path=normalized,
                remote_path=location.path,
            )
        )


def _stage_item_row(
    run_id: str,
    connection_id: int,
    item: RemoteItem,
    *,
    secret: str = "",
) -> dict:
    normalized_path = (
        normalize_connector_path(item.remote_path).display if item.remote_path else None
    )
    return {
        "sync_run_id": run_id,
        "connection_id": connection_id,
        "remote_id": item.remote_id,
        "library_remote_id": item.library_remote_id,
        "item_type": item.item_type,
        "remote_path": item.remote_path,
        "normalized_remote_path": normalized_path,
        "title": item.title,
        "original_title": item.original_title,
        "series_name": item.series_name,
        "season_name": item.season_name,
        "index_number": item.index_number,
        "parent_index_number": item.parent_index_number,
        "date_created": item.date_created,
        "premiere_date": item.premiere_date,
        "production_year": item.production_year,
        "overview": item.overview,
        "provider_ids": item.provider_ids,
        "size_bytes": item.size_bytes,
        "duration_seconds": item.duration_seconds,
        "core_payload": {},
        "provider_payload": public_connector_payload(
            item.provider_payload,
            secrets=(secret,),
        ),
        "last_synced_at": utc_now(),
    }


def _upsert_stage_items(db: Session, rows: list[dict]) -> None:
    if not rows:
        return
    statement = sqlite_insert(ConnectorSyncStageItem)
    excluded = statement.excluded
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[
                ConnectorSyncStageItem.sync_run_id,
                ConnectorSyncStageItem.connection_id,
                ConnectorSyncStageItem.remote_id,
            ],
            set_={
                "library_remote_id": excluded.library_remote_id,
                "item_type": excluded.item_type,
                "remote_path": excluded.remote_path,
                "normalized_remote_path": excluded.normalized_remote_path,
                "title": excluded.title,
                "original_title": excluded.original_title,
                "series_name": excluded.series_name,
                "season_name": excluded.season_name,
                "index_number": excluded.index_number,
                "parent_index_number": excluded.parent_index_number,
                "date_created": excluded.date_created,
                "premiere_date": excluded.premiere_date,
                "production_year": excluded.production_year,
                "overview": excluded.overview,
                "provider_ids": excluded.provider_ids,
                "size_bytes": excluded.size_bytes,
                "duration_seconds": excluded.duration_seconds,
                "core_payload": excluded.core_payload,
                "provider_payload": excluded.provider_payload,
                "last_synced_at": excluded.last_synced_at,
            },
        ),
        rows,
    )


def promote_connector_staging(
    db: Session,
    run_id: str,
    connection_id: int,
    *,
    commit: bool = True,
) -> dict[str, int]:
    stage_libraries = list(
        db.scalars(
            select(ConnectorSyncStageLibrary).where(
                ConnectorSyncStageLibrary.sync_run_id == run_id,
                ConnectorSyncStageLibrary.connection_id == connection_id,
            )
        )
    )
    live_libraries = {
        library.remote_id: library
        for library in db.scalars(
            select(ConnectorLibrary).where(ConnectorLibrary.connection_id == connection_id)
        )
    }
    promoted_libraries: dict[str, ConnectorLibrary] = {}
    for staged in stage_libraries:
        live = live_libraries.get(staged.remote_id)
        if live is None:
            live = ConnectorLibrary(connection_id=connection_id, remote_id=staged.remote_id)
            db.add(live)
        live.name = staged.name
        live.media_type = staged.media_type
        live.provider_payload = staged.provider_payload
        live.last_synced_at = staged.last_synced_at
        promoted_libraries[staged.remote_id] = live
    db.flush()

    stage_locations = list(
        db.scalars(
            select(ConnectorSyncStageLocation).where(
                ConnectorSyncStageLocation.sync_run_id == run_id,
                ConnectorSyncStageLocation.connection_id == connection_id,
            )
        )
    )
    desired_location_keys: set[tuple[int, str]] = set()
    for staged in stage_locations:
        library = promoted_libraries.get(staged.library_remote_id)
        if library is None:
            continue
        desired_location_keys.add((library.id, staged.normalized_path))
        live = db.scalar(
            select(ConnectorLibraryLocation).where(
                ConnectorLibraryLocation.connector_library_id == library.id,
                ConnectorLibraryLocation.normalized_path == staged.normalized_path,
            )
        )
        if live is None:
            live = ConnectorLibraryLocation(
                connector_library_id=library.id,
                normalized_path=staged.normalized_path,
                remote_path=staged.remote_path,
            )
            db.add(live)
        else:
            live.remote_path = staged.remote_path

    stage_item_count = int(
        db.scalar(
            select(func.count()).select_from(ConnectorSyncStageItem).where(
                ConnectorSyncStageItem.sync_run_id == run_id,
                ConnectorSyncStageItem.connection_id == connection_id,
            )
        )
        or 0
    )
    now = utc_now()
    item_columns = (
        "connection_id",
        "connector_library_id",
        "remote_id",
        "item_type",
        "remote_path",
        "normalized_remote_path",
        "title",
        "original_title",
        "series_name",
        "season_name",
        "index_number",
        "parent_index_number",
        "date_created",
        "premiere_date",
        "production_year",
        "overview",
        "provider_ids",
        "provider_payload",
        "size_bytes",
        "duration_seconds",
        "match_status",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    item_source = (
        select(
            ConnectorSyncStageItem.connection_id,
            ConnectorLibrary.id,
            ConnectorSyncStageItem.remote_id,
            ConnectorSyncStageItem.item_type,
            ConnectorSyncStageItem.remote_path,
            ConnectorSyncStageItem.normalized_remote_path,
            ConnectorSyncStageItem.title,
            ConnectorSyncStageItem.original_title,
            ConnectorSyncStageItem.series_name,
            ConnectorSyncStageItem.season_name,
            ConnectorSyncStageItem.index_number,
            ConnectorSyncStageItem.parent_index_number,
            ConnectorSyncStageItem.date_created,
            ConnectorSyncStageItem.premiere_date,
            ConnectorSyncStageItem.production_year,
            ConnectorSyncStageItem.overview,
            ConnectorSyncStageItem.provider_ids,
            ConnectorSyncStageItem.provider_payload,
            ConnectorSyncStageItem.size_bytes,
            ConnectorSyncStageItem.duration_seconds,
            literal("unmapped"),
            ConnectorSyncStageItem.last_synced_at,
            literal(now, type_=UTCDateTime()),
            literal(now, type_=UTCDateTime()),
        )
        .outerjoin(
            ConnectorLibrary,
            (ConnectorLibrary.connection_id == ConnectorSyncStageItem.connection_id)
            & (ConnectorLibrary.remote_id == ConnectorSyncStageItem.library_remote_id),
        )
        .where(
            ConnectorSyncStageItem.sync_run_id == run_id,
            ConnectorSyncStageItem.connection_id == connection_id,
        )
    )
    item_insert = sqlite_insert(ConnectorItem).from_select(item_columns, item_source)
    db.execute(
        item_insert.on_conflict_do_update(
            index_elements=[ConnectorItem.connection_id, ConnectorItem.remote_id],
            set_={
                "connector_library_id": item_insert.excluded.connector_library_id,
                "item_type": item_insert.excluded.item_type,
                "remote_path": item_insert.excluded.remote_path,
                "normalized_remote_path": item_insert.excluded.normalized_remote_path,
                "title": item_insert.excluded.title,
                "original_title": item_insert.excluded.original_title,
                "series_name": item_insert.excluded.series_name,
                "season_name": item_insert.excluded.season_name,
                "index_number": item_insert.excluded.index_number,
                "parent_index_number": item_insert.excluded.parent_index_number,
                "date_created": item_insert.excluded.date_created,
                "premiere_date": item_insert.excluded.premiere_date,
                "production_year": item_insert.excluded.production_year,
                "overview": item_insert.excluded.overview,
                "provider_ids": item_insert.excluded.provider_ids,
                "provider_payload": item_insert.excluded.provider_payload,
                "size_bytes": item_insert.excluded.size_bytes,
                "duration_seconds": item_insert.excluded.duration_seconds,
                "last_synced_at": item_insert.excluded.last_synced_at,
                "updated_at": now,
            },
        )
    )
    staged_item_exists = (
        select(ConnectorSyncStageItem.remote_id)
        .where(
            ConnectorSyncStageItem.sync_run_id == run_id,
            ConnectorSyncStageItem.connection_id == connection_id,
            ConnectorSyncStageItem.remote_id == ConnectorItem.remote_id,
        )
        .exists()
    )
    stale_delete_result = db.execute(
        delete(ConnectorItem).where(
            ConnectorItem.connection_id == connection_id,
            ~staged_item_exists,
        )
    )
    stale_item_count = max(0, int(stale_delete_result.rowcount or 0))

    live_locations = db.scalars(
        select(ConnectorLibraryLocation)
        .join(ConnectorLibrary)
        .where(ConnectorLibrary.connection_id == connection_id)
    ).all()
    stale_location_ids = [
        location.id
        for location in live_locations
        if (location.connector_library_id, location.normalized_path) not in desired_location_keys
    ]
    if stale_location_ids:
        db.execute(
            delete(ConnectorLibraryLocation).where(
                ConnectorLibraryLocation.id.in_(stale_location_ids)
            )
        )

    stale_library_ids = [
        library.id
        for remote_id, library in live_libraries.items()
        if remote_id not in promoted_libraries
    ]
    if stale_library_ids:
        db.execute(delete(ConnectorLibrary).where(ConnectorLibrary.id.in_(stale_library_ids)))
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "libraries": len(stage_libraries),
        "locations": len(stage_locations),
        "items": stage_item_count,
        "deleted_libraries": len(stale_library_ids),
        "deleted_locations": len(stale_location_ids),
        "deleted_items": stale_item_count,
    }


def cleanup_connector_staging(
    db: Session,
    run_id: str,
    connection_id: int,
    *,
    commit: bool = True,
) -> None:
    for model in (ConnectorSyncStageItem, ConnectorSyncStageLocation, ConnectorSyncStageLibrary):
        db.execute(
            delete(model).where(
                model.sync_run_id == run_id,
                model.connection_id == connection_id,
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()


def run_connector_sync(db: Session, job_id: int) -> dict:
    job = db.get(ConnectorSyncJob, job_id)
    if job is None:
        raise ValueError("Connector sync job not found")
    if job.status != JobStatus.running or job.cancellation_requested:
        raise ConnectorSyncCancelled("Connector synchronization is no longer runnable")
    connection = db.get(ConnectorConnection, job.connection_id)
    if connection is None:
        raise ValueError("Connector connection not found")
    secret = read_connector_secret(db, connection.id)
    if not connection.base_url or not secret:
        raise ValueError("Connector URL and secret are required before synchronization")

    run_id = uuid4().hex
    job.sync_run_id = run_id
    job.started_at = job.started_at or utc_now()
    job.heartbeat_at = utc_now()
    job.progress_phase = "connecting"
    connection.last_sync_started_at = job.started_at
    connection.last_status = "syncing"
    connection.last_error = None
    db.commit()

    try:
        cancellation = lambda: _check_cancellation(db, job_id)
        with connector_registry.create(connection, secret, cancellation) as adapter:
            server = adapter.get_server_info()
            connection.server_name = server.name
            connection.server_version = server.version
            connection.capabilities = {capability: True for capability in adapter.capabilities}
            db.commit()
            _update_progress(db, job, "libraries", 0)
            libraries = list(adapter.iter_libraries())
            for index, library in enumerate(libraries, start=1):
                cancellation()
                _stage_library(db, run_id, connection.id, library, secret=secret)
                if index % 100 == 0:
                    db.commit()
                    _update_progress(db, job, "libraries", index, len(libraries))
            db.commit()
            _update_progress(db, job, "items", 0)
            item_count = 0
            item_batch: list[dict] = []
            for item_count, item in enumerate(adapter.iter_items(libraries), start=1):
                item_batch.append(
                    _stage_item_row(run_id, connection.id, item, secret=secret)
                )
                if len(item_batch) >= 500:
                    cancellation()
                    _upsert_stage_items(db, item_batch)
                    item_batch.clear()
                    db.commit()
                    _update_progress(db, job, "items", item_count)
            cancellation()
            _upsert_stage_items(db, item_batch)
            db.commit()
        _update_progress(db, job, "promoting", item_count)
        summary = promote_connector_staging(db, run_id, connection.id, commit=False)
        job.progress_phase = "matching"
        job.progress_current = item_count
        job.heartbeat_at = utc_now()
        db.flush()
        summary["matching"] = recompute_connector_matches(
            db,
            connection_id=connection.id,
            cancellation_check=lambda: _check_cancellation(db, job_id),
            commit=False,
        )
        cleanup_connector_staging(db, run_id, connection.id, commit=False)
        finished = utc_now()
        job = db.get(ConnectorSyncJob, job_id)
        connection = db.get(ConnectorConnection, connection.id)
        job.status = JobStatus.completed
        job.active_lock = None
        job.finished_at = finished
        job.progress_phase = "completed"
        job.sync_run_id = None
        job.sync_summary = summary
        connection.last_status = "success"
        connection.last_sync_finished_at = finished
        connection.last_successful_sync_at = finished
        db.commit()
        stats_cache.invalidate(str(id(db.get_bind())))
        return summary
    except Exception as exc:
        db.rollback()
        cleanup_connector_staging(db, run_id, connection.id)
        job = db.get(ConnectorSyncJob, job_id)
        connection = db.get(ConnectorConnection, connection.id)
        finished = utc_now()
        canceled = isinstance(exc, ConnectorSyncCancelled)
        safe_error = redact_connector_error(exc, secrets=(secret,))
        if job is not None:
            job.status = JobStatus.canceled if canceled else JobStatus.failed
            job.active_lock = None
            job.finished_at = finished
            job.progress_phase = "canceled" if canceled else "failed"
            job.sync_run_id = None
            job.error = safe_error
        if connection is not None:
            connection.last_status = "canceled" if canceled else "failed"
            connection.last_error = safe_error
            connection.last_sync_finished_at = finished
        db.commit()
        if canceled:
            raise ConnectorSyncCancelled(safe_error) from None
        raise ConnectorSyncFailed(safe_error) from None


def run_connector_recompute(db: Session, job_id: int) -> dict[str, int]:
    job = db.get(ConnectorSyncJob, job_id)
    if job is None:
        raise ValueError("Connector recompute job not found")
    if job.status != JobStatus.running or job.cancellation_requested:
        raise ConnectorSyncCancelled("Connector recompute is no longer runnable")
    if db.get(ConnectorConnection, job.connection_id) is None:
        raise ValueError("Connector connection not found")
    try:
        job.progress_phase = "matching"
        job.progress_current = 0
        job.heartbeat_at = utc_now()
        db.commit()
        matching = recompute_connector_matches(
            db,
            connection_id=job.connection_id,
            cancellation_check=lambda: _check_cancellation(db, job_id),
            commit=False,
        )
        _check_cancellation(db, job_id)
        job = db.get(ConnectorSyncJob, job_id)
        job.status = JobStatus.completed
        job.active_lock = None
        job.finished_at = utc_now()
        job.progress_phase = "completed"
        job.progress_current = sum(matching.values())
        job.sync_summary = {"matching": matching}
        db.commit()
        stats_cache.invalidate(str(id(db.get_bind())))
        return matching
    except Exception as exc:
        db.rollback()
        safe_error = redact_connector_error(exc)
        job = db.get(ConnectorSyncJob, job_id)
        canceled = isinstance(exc, ConnectorSyncCancelled)
        if job is not None:
            job.status = JobStatus.canceled if canceled else JobStatus.failed
            job.active_lock = None
            job.finished_at = utc_now()
            job.progress_phase = "canceled" if canceled else "failed"
            job.error = safe_error
        db.commit()
        if canceled:
            raise ConnectorSyncCancelled(safe_error) from None
        raise ConnectorSyncFailed(safe_error) from None


def mirror_legacy_jellyfin_snapshot(db: Session) -> tuple[int | None, dict[str, int]]:
    """Mirror the compatibility catalog without exposing Jellyfin fields to core sync code."""
    connection = db.scalar(
        select(ConnectorConnection).where(
            ConnectorConnection.provider == "jellyfin",
            ConnectorConnection.name == "Jellyfin",
        )
    )
    legacy_connection = db.get(JellyfinConnection, 1)
    if connection is None or legacy_connection is None:
        return None, {}
    connection.base_url = legacy_connection.base_url
    connection.enabled = legacy_connection.enabled
    connection.sync_interval_minutes = legacy_connection.sync_interval_minutes
    connection.server_name = legacy_connection.server_name
    connection.server_version = legacy_connection.server_version
    connection.last_status = legacy_connection.last_status
    connection.last_error = legacy_connection.last_error
    connection.last_sync_started_at = legacy_connection.last_sync_started_at
    connection.last_sync_finished_at = legacy_connection.last_sync_finished_at
    connection.last_successful_sync_at = legacy_connection.last_successful_sync_at
    connection.capabilities = {
        "users": True,
        "user_states": True,
        "playback_events": True,
        "images": True,
    }

    live_libraries = {
        library.remote_id: library
        for library in db.scalars(
            select(ConnectorLibrary).where(ConnectorLibrary.connection_id == connection.id)
        )
    }
    desired_library_ids: set[str] = set()
    legacy_library_map: dict[int, ConnectorLibrary] = {}
    for legacy in db.scalars(select(JellyfinLibrary)):
        remote_id = legacy.remote_item_id or f"legacy:{legacy.id}"
        desired_library_ids.add(remote_id)
        current = live_libraries.get(remote_id)
        if current is None:
            current = ConnectorLibrary(connection_id=connection.id, remote_id=remote_id)
            db.add(current)
        current.name = legacy.name
        current.media_type = legacy.collection_type
        current.last_synced_at = legacy.last_synced_at
        current.provider_payload = {}
        db.flush()
        legacy_library_map[legacy.id] = current
        desired_locations: set[str] = set()
        for path in legacy.locations or []:
            try:
                normalized = normalize_connector_path(str(path)).display
            except ValueError:
                continue
            desired_locations.add(normalized)
            location = db.scalar(
                select(ConnectorLibraryLocation).where(
                    ConnectorLibraryLocation.connector_library_id == current.id,
                    ConnectorLibraryLocation.normalized_path == normalized,
                )
            )
            if location is None:
                db.add(
                    ConnectorLibraryLocation(
                        connector_library_id=current.id,
                        remote_path=str(path),
                        normalized_path=normalized,
                    )
                )
            else:
                location.remote_path = str(path)
        stale_locations = list(
            db.scalars(
                select(ConnectorLibraryLocation).where(
                    ConnectorLibraryLocation.connector_library_id == current.id,
                    ConnectorLibraryLocation.normalized_path.not_in(desired_locations),
                )
            )
        ) if desired_locations else list(
            db.scalars(
                select(ConnectorLibraryLocation).where(
                    ConnectorLibraryLocation.connector_library_id == current.id
                )
            )
        )
        for location in stale_locations:
            db.delete(location)

    legacy_items = list(db.scalars(select(JellyfinItem)))
    live_items = {
        item.remote_id: item
        for item in db.scalars(
            select(ConnectorItem).where(ConnectorItem.connection_id == connection.id)
        )
    }
    desired_item_ids: set[str] = set()
    for legacy in legacy_items:
        desired_item_ids.add(legacy.jellyfin_item_id)
        item = live_items.get(legacy.jellyfin_item_id)
        if item is None:
            item = ConnectorItem(connection_id=connection.id, remote_id=legacy.jellyfin_item_id)
            db.add(item)
        connector_library = legacy_library_map.get(legacy.library_id) if legacy.library_id else None
        item.connector_library_id = connector_library.id if connector_library else None
        item.item_type = legacy.item_type
        item.remote_path = legacy.path
        item.normalized_remote_path = (
            normalize_connector_path(legacy.path).display if legacy.path else None
        )
        item.title = legacy.title
        item.original_title = legacy.original_title
        item.series_name = legacy.series_name
        item.season_name = legacy.season_name
        item.index_number = legacy.index_number
        item.parent_index_number = legacy.parent_index_number
        item.date_created = legacy.date_created
        item.premiere_date = legacy.premiere_date
        item.production_year = legacy.production_year
        item.overview = legacy.overview
        item.provider_ids = legacy.provider_ids or {}
        item.provider_payload = {
            **(legacy.raw_limited_payload or {}),
            "image_tags": legacy.image_tags or {},
            "backdrop_image_tags": legacy.backdrop_image_tags or [],
        }
        item.size_bytes = legacy.size_bytes
        item.duration_seconds = legacy.duration_seconds
        item.last_synced_at = legacy.last_synced_at
        if item.match_status != "ignored":
            item.match_status = "unmapped"
            item.mismatch_reason = None

    for remote_id, item in live_items.items():
        if remote_id not in desired_item_ids:
            db.delete(item)
    for remote_id, library in live_libraries.items():
        if remote_id not in desired_library_ids:
            db.delete(library)
    db.commit()
    matching = recompute_connector_matches(db, connection_id=connection.id)
    return connection.id, matching
