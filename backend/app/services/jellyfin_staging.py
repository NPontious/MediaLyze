from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, exists, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    JellyfinItem,
    JellyfinLibrary,
    JellyfinSyncStageItem,
    JellyfinSyncStageLibrary,
    JellyfinSyncStageUser,
    JellyfinSyncStageUserData,
    JellyfinUser,
    JellyfinUserItemData,
)
from backend.app.db.types import UTCDateTime
from backend.app.utils.time import utc_now


STAGE_MODELS = (
    JellyfinSyncStageUserData,
    JellyfinSyncStageItem,
    JellyfinSyncStageLibrary,
    JellyfinSyncStageUser,
)


def bulk_upsert_stage(
    db: Session,
    model,
    rows: Iterable[dict],
    *,
    conflict_columns: tuple[str, ...],
) -> int:
    """Write one remote page with SQLite's native multi-row UPSERT."""
    values = list(rows)
    if not values:
        return 0
    statement = sqlite_insert(model).values(values)
    excluded = statement.excluded
    update_values = {
        column.name: getattr(excluded, column.name)
        for column in model.__table__.columns
        if column.name not in conflict_columns
    }
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[getattr(model, name) for name in conflict_columns],
            set_=update_values,
        )
    )
    return len(values)


def commit_stage_page(
    db: Session,
    model,
    rows: Iterable[dict],
    *,
    conflict_columns: tuple[str, ...],
) -> int:
    count = bulk_upsert_stage(db, model, rows, conflict_columns=conflict_columns)
    db.commit()
    return count


def cleanup_staging(db: Session, sync_run_id: str, *, commit: bool = True) -> None:
    for model in STAGE_MODELS:
        db.execute(delete(model).where(model.sync_run_id == sync_run_id))
    if commit:
        db.commit()


def cleanup_all_staging(db: Session, *, commit: bool = True) -> None:
    for model in STAGE_MODELS:
        db.execute(delete(model))
    if commit:
        db.commit()


def _promote_users(db: Session, sync_run_id: str, now) -> None:
    stage = JellyfinSyncStageUser
    columns = (
        "jellyfin_user_id",
        "name",
        "enabled_for_sync",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    source = select(
        stage.jellyfin_user_id,
        stage.name,
        stage.enabled_for_sync,
        stage.last_synced_at,
        literal(now, type_=UTCDateTime()),
        literal(now, type_=UTCDateTime()),
    ).where(stage.sync_run_id == sync_run_id)
    statement = sqlite_insert(JellyfinUser).from_select(columns, source)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[JellyfinUser.jellyfin_user_id],
            set_={
                "name": statement.excluded.name,
                "enabled_for_sync": statement.excluded.enabled_for_sync,
                "last_synced_at": statement.excluded.last_synced_at,
                "updated_at": now,
            },
        )
    )


def _promote_libraries(db: Session, sync_run_id: str, now) -> None:
    stage = JellyfinSyncStageLibrary
    # Preserve manual links on databases created before stable remote IDs existed.
    staged_rows = list(db.scalars(select(stage).where(stage.sync_run_id == sync_run_id)))
    for staged in staged_rows:
        existing = db.scalar(
            select(JellyfinLibrary).where(JellyfinLibrary.remote_item_id == staged.remote_item_id)
        )
        if existing is not None:
            continue
        legacy = db.scalar(
            select(JellyfinLibrary).where(
                JellyfinLibrary.name == staged.name,
                (JellyfinLibrary.remote_item_id.is_(None))
                | JellyfinLibrary.remote_item_id.like("legacy:%"),
            )
        )
        if legacy is not None:
            legacy.remote_item_id = staged.remote_item_id
    db.flush()

    columns = (
        "remote_item_id",
        "name",
        "collection_type",
        "locations",
        "mapped_locations",
        "mapped_status",
        "linked_library_id",
        "link_method",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    source = select(
        stage.remote_item_id,
        stage.name,
        stage.collection_type,
        stage.locations,
        stage.mapped_locations,
        stage.mapped_status,
        stage.linked_library_id,
        stage.link_method,
        stage.last_synced_at,
        literal(now, type_=UTCDateTime()),
        literal(now, type_=UTCDateTime()),
    ).where(stage.sync_run_id == sync_run_id)
    statement = sqlite_insert(JellyfinLibrary).from_select(columns, source)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[JellyfinLibrary.remote_item_id],
            set_={
                "name": statement.excluded.name,
                "collection_type": statement.excluded.collection_type,
                "locations": statement.excluded.locations,
                "mapped_locations": statement.excluded.mapped_locations,
                "mapped_status": statement.excluded.mapped_status,
                "linked_library_id": statement.excluded.linked_library_id,
                "link_method": statement.excluded.link_method,
                "last_synced_at": statement.excluded.last_synced_at,
                "updated_at": now,
            },
        )
    )


def _promote_items(db: Session, sync_run_id: str, now) -> None:
    stage = JellyfinSyncStageItem
    columns = (
        "jellyfin_item_id", "library_id", "library_name", "item_type", "path",
        "parent_id", "series_id", "season_id", "title", "original_title",
        "series_name", "season_name", "index_number", "parent_index_number",
        "date_created", "premiere_date", "production_year", "overview",
        "provider_ids", "image_tags", "backdrop_image_tags", "raw_limited_payload",
        "size_bytes", "duration_seconds", "last_synced_at", "created_at", "updated_at",
    )
    source = (
        select(
            stage.jellyfin_item_id,
            JellyfinLibrary.id,
            stage.library_name,
            stage.item_type,
            stage.path,
            stage.parent_id,
            stage.series_id,
            stage.season_id,
            stage.title,
            stage.original_title,
            stage.series_name,
            stage.season_name,
            stage.index_number,
            stage.parent_index_number,
            stage.date_created,
            stage.premiere_date,
            stage.production_year,
            stage.overview,
            stage.provider_ids,
            stage.image_tags,
            stage.backdrop_image_tags,
            stage.raw_limited_payload,
            stage.size_bytes,
            stage.duration_seconds,
            stage.last_synced_at,
            literal(now, type_=UTCDateTime()),
            literal(now, type_=UTCDateTime()),
        )
        .select_from(stage)
        .outerjoin(JellyfinLibrary, JellyfinLibrary.remote_item_id == stage.library_remote_item_id)
        .where(stage.sync_run_id == sync_run_id)
    )
    statement = sqlite_insert(JellyfinItem).from_select(columns, source)
    update_values = {
        name: getattr(statement.excluded, name)
        for name in columns
        if name not in {"jellyfin_item_id", "created_at"}
    }
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[JellyfinItem.jellyfin_item_id],
            set_=update_values,
        )
    )


def _promote_user_data(db: Session, sync_run_id: str) -> None:
    stage = JellyfinSyncStageUserData
    db.execute(delete(JellyfinUserItemData))
    columns = (
        "jellyfin_item_id",
        "jellyfin_user_id",
        "play_count",
        "played",
        "playback_position_ticks",
        "last_played_date",
        "is_favorite",
        "last_synced_at",
    )
    source = (
        select(
            JellyfinItem.id,
            stage.jellyfin_user_id,
            stage.play_count,
            stage.played,
            stage.playback_position_ticks,
            stage.last_played_date,
            stage.is_favorite,
            stage.last_synced_at,
        )
        .select_from(stage)
        .join(JellyfinItem, JellyfinItem.jellyfin_item_id == stage.jellyfin_item_id)
        .join(JellyfinUser, JellyfinUser.jellyfin_user_id == stage.jellyfin_user_id)
        .where(stage.sync_run_id == sync_run_id, JellyfinUser.enabled_for_sync.is_(True))
    )
    db.execute(sqlite_insert(JellyfinUserItemData).from_select(columns, source))


def promote_staging(db: Session, sync_run_id: str) -> None:
    """Atomically replace the visible Jellyfin snapshot from a completed stage."""
    now = utc_now()
    try:
        _promote_users(db, sync_run_id, now)
        _promote_libraries(db, sync_run_id, now)
        _promote_items(db, sync_run_id, now)

        staged_item_exists = exists(
            select(JellyfinSyncStageItem.jellyfin_item_id).where(
                JellyfinSyncStageItem.sync_run_id == sync_run_id,
                JellyfinSyncStageItem.jellyfin_item_id == JellyfinItem.jellyfin_item_id,
            )
        )
        db.execute(delete(JellyfinItem).where(~staged_item_exists))
        _promote_user_data(db, sync_run_id)

        staged_library_exists = exists(
            select(JellyfinSyncStageLibrary.remote_item_id).where(
                JellyfinSyncStageLibrary.sync_run_id == sync_run_id,
                JellyfinSyncStageLibrary.remote_item_id == JellyfinLibrary.remote_item_id,
            )
        )
        db.execute(delete(JellyfinLibrary).where(~staged_library_exists))
        staged_user_exists = exists(
            select(JellyfinSyncStageUser.jellyfin_user_id).where(
                JellyfinSyncStageUser.sync_run_id == sync_run_id,
                JellyfinSyncStageUser.jellyfin_user_id == JellyfinUser.jellyfin_user_id,
            )
        )
        db.execute(delete(JellyfinUser).where(~staged_user_exists))
        db.commit()
        db.expire_all()
    except Exception:
        db.rollback()
        raise
