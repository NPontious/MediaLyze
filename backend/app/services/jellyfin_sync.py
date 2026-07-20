from __future__ import annotations

from datetime import datetime
from contextlib import nullcontext
from collections.abc import Iterator

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.entities import (
    JellyfinConnection,
    JellyfinItem,
    JellyfinLibrary,
    JellyfinPathMapping,
    JellyfinUser,
    JellyfinUserItemData,
    Library,
)
from backend.app.core.config import get_settings
from backend.app.services.jellyfin_client import JellyfinClient, JellyfinError, JellyfinItemPage
from backend.app.services.jellyfin_credentials import read_jellyfin_api_key
from backend.app.services.jellyfin_matching import (
    map_library_locations,
    normalize_jellyfin_path,
    recompute_jellyfin_matches,
)
from backend.app.services.jellyfin_progress import (
    begin_jellyfin_progress,
    clear_jellyfin_progress,
    jellyfin_cancellation_requested,
    reset_jellyfin_cancellation,
    update_jellyfin_progress,
)
from backend.app.utils.time import utc_now


class JellyfinSyncCancelled(JellyfinError):
    pass


def _raise_if_sync_cancelled() -> None:
    if jellyfin_cancellation_requested():
        raise JellyfinSyncCancelled("Jellyfin synchronization was canceled")


def _iter_item_pages(
    client: object,
    *,
    user_id: str | None = None,
    user_data_only: bool = False,
    progress_callback=None,
) -> Iterator[JellyfinItemPage]:
    iterator = getattr(client, "iter_item_pages", None)
    if callable(iterator):
        yield from iterator(
            user_id=user_id,
            user_data_only=user_data_only,
            progress_callback=progress_callback,
        )
        return
    # Compatibility for small test/fake clients and third-party extensions that
    # still implement the pre-streaming get_items contract.
    payloads = client.get_items(user_id=user_id, progress_callback=progress_callback)  # type: ignore[attr-defined]
    yield JellyfinItemPage(items=payloads, start_index=0, total_record_count=len(payloads))


def get_or_create_jellyfin_connection(db: Session) -> JellyfinConnection:
    connection = db.get(JellyfinConnection, 1)
    if connection is None:
        connection = JellyfinConnection(id=1)
        db.add(connection)
        db.flush()
    return connection


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _folder_remote_id(folder: dict) -> str:
    remote_id = str(folder.get("ItemId") or "").strip()
    if remote_id:
        return remote_id
    name = str(folder.get("Name") or "").strip()
    locations = "|".join(sorted(str(value) for value in folder.get("Locations") or []))
    return f"legacy:{name}:{locations}"


def _library_for_path(
    path: str | None,
    folders: list[dict],
    libraries_by_remote_id: dict[str, JellyfinLibrary],
) -> JellyfinLibrary | None:
    if not path:
        return None
    normalized = normalize_jellyfin_path(path)
    candidates: list[tuple[int, JellyfinLibrary]] = []
    for folder in folders:
        library = libraries_by_remote_id.get(_folder_remote_id(folder))
        if library is None:
            continue
        for location in folder.get("Locations") or []:
            prefix = normalize_jellyfin_path(str(location))
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                candidates.append((len(prefix), library))
    return max(candidates, key=lambda pair: pair[0], default=(0, None))[1]


def _upsert_item(
    db: Session,
    payload: dict,
    folders: list[dict],
    libraries_by_remote_id: dict[str, JellyfinLibrary],
    existing: dict[str, JellyfinItem],
    now: datetime,
) -> JellyfinItem | None:
    jellyfin_id = str(payload.get("Id") or "").strip()
    if not jellyfin_id:
        return None
    item = existing.get(jellyfin_id)
    if item is None:
        item = JellyfinItem(
            jellyfin_item_id=jellyfin_id,
            item_type=str(payload.get("Type") or "Unknown"),
            title=str(payload.get("Name") or "Untitled"),
        )
        db.add(item)
        existing[jellyfin_id] = item
    item.item_type = str(payload.get("Type") or "Unknown")
    item.path = str(payload.get("Path")) if payload.get("Path") else None
    library = _library_for_path(item.path, folders, libraries_by_remote_id)
    item.library_id = library.id if library else None
    item.library_name = library.name if library else None
    item.parent_id = payload.get("ParentId")
    item.series_id = payload.get("SeriesId")
    item.season_id = payload.get("SeasonId")
    item.title = str(payload.get("Name") or "Untitled")
    item.original_title = payload.get("OriginalTitle")
    item.series_name = payload.get("SeriesName")
    item.season_name = payload.get("SeasonName")
    item.index_number = payload.get("IndexNumber")
    item.parent_index_number = payload.get("ParentIndexNumber")
    item.date_created = _parse_datetime(payload.get("DateCreated"))
    item.premiere_date = _parse_datetime(payload.get("PremiereDate"))
    item.production_year = payload.get("ProductionYear")
    item.overview = payload.get("Overview")
    item.provider_ids = payload.get("ProviderIds") or {}
    item.image_tags = payload.get("ImageTags") or {}
    item.backdrop_image_tags = payload.get("BackdropImageTags") or []
    limited_payload = {
        key: payload[key]
        for key in ("Size", "RunTimeTicks", "MediaType", "CollectionType", "IsFolder")
        if key in payload
    }
    if "Size" not in limited_payload:
        media_sources = payload.get("MediaSources")
        if isinstance(media_sources, list):
            source_size = next(
                (
                    source.get("Size")
                    for source in media_sources
                    if isinstance(source, dict) and isinstance(source.get("Size"), (int, float))
                ),
                None,
            )
            if source_size is not None:
                limited_payload["Size"] = source_size
    item.raw_limited_payload = limited_payload
    raw_size = limited_payload.get("Size")
    item.size_bytes = int(raw_size) if isinstance(raw_size, (int, float)) and raw_size >= 0 else None
    runtime_ticks = limited_payload.get("RunTimeTicks")
    item.duration_seconds = (
        float(runtime_ticks) / 10_000_000
        if isinstance(runtime_ticks, (int, float)) and runtime_ticks >= 0
        else None
    )
    item.last_synced_at = now
    return item


def _upsert_user_data(
    db: Session,
    item: JellyfinItem,
    user: JellyfinUser,
    payload: dict,
    existing: dict[int, JellyfinUserItemData],
    now: datetime,
) -> None:
    data = existing.get(item.id)
    if data is None:
        data = JellyfinUserItemData(
            jellyfin_item_id=item.id,
            jellyfin_user_id=user.jellyfin_user_id,
        )
        db.add(data)
        existing[item.id] = data
    data.play_count = int(payload.get("PlayCount") or 0)
    data.played = bool(payload.get("Played"))
    data.playback_position_ticks = int(payload.get("PlaybackPositionTicks") or 0)
    data.last_played_date = _parse_datetime(payload.get("LastPlayedDate"))
    data.is_favorite = bool(payload.get("IsFavorite"))
    data.last_synced_at = now


def _sync_libraries(
    db: Session, folders: list[dict], now: datetime
) -> dict[str, JellyfinLibrary]:
    mappings = list(db.scalars(select(JellyfinPathMapping).where(JellyfinPathMapping.enabled.is_(True))))
    medialyze_libraries = list(db.scalars(select(Library).options(selectinload(Library.roots))))
    medialyze_library_ids = {library.id for library in medialyze_libraries}
    existing_records = list(db.scalars(select(JellyfinLibrary)))
    existing_by_remote_id = {
        library.remote_item_id: library
        for library in existing_records
        if library.remote_item_id
    }
    existing_by_name: dict[str, list[JellyfinLibrary]] = {}
    for library in existing_records:
        existing_by_name.setdefault(library.name, []).append(library)
    seen: set[str] = set()
    for folder in folders:
        name = str(folder.get("Name") or "").strip()
        if not name:
            continue
        remote_id = _folder_remote_id(folder)
        seen.add(remote_id)
        locations = [str(location) for location in folder.get("Locations") or []]
        mapped_locations, status = map_library_locations(locations, mappings)
        record = existing_by_remote_id.get(remote_id)
        if record is None:
            # Upgrade pre-ID rows once without treating a rename as delete/create.
            legacy_candidates = existing_by_name.get(name, [])
            record = next(
                (
                    candidate
                    for candidate in legacy_candidates
                    if not candidate.remote_item_id
                    or str(candidate.remote_item_id).startswith("legacy:")
                ),
                None,
            )
        if record is None:
            record = JellyfinLibrary(name=name, remote_item_id=remote_id)
            db.add(record)
        record.remote_item_id = remote_id
        record.name = name
        record.collection_type = folder.get("CollectionType")
        record.locations = locations
        record.mapped_locations = mapped_locations
        record.mapped_status = status
        record.last_synced_at = now
        if record.link_method == "manual":
            if record.linked_library_id in medialyze_library_ids:
                record.mapped_status = "linked"
            continue
        record.linked_library_id = None
        record.link_method = None
        mapped_keys = {normalize_jellyfin_path(path) for path in mapped_locations}
        for medialyze_library in medialyze_libraries:
            root_keys = {
                normalize_jellyfin_path(root.path)
                for root in medialyze_library.roots
            } or {normalize_jellyfin_path(medialyze_library.path)}
            if mapped_keys & root_keys:
                record.linked_library_id = medialyze_library.id
                record.link_method = "path"
                record.mapped_status = "linked"
                break
        existing_by_remote_id[remote_id] = record
    for record in existing_records:
        if record.remote_item_id not in seen:
            db.delete(record)
    db.flush()
    return {
        remote_id: record
        for remote_id, record in existing_by_remote_id.items()
        if remote_id in seen
    }


def run_jellyfin_sync(db: Session, *, job_id: int | None = None) -> dict[str, int | str]:
    connection = get_or_create_jellyfin_connection(db)
    if not connection.enabled:
        raise JellyfinError("Jellyfin integration is disabled")
    api_key = read_jellyfin_api_key(connection, get_settings().jellyfin_api_key_file)
    if job_id is None:
        reset_jellyfin_cancellation()
    begin_jellyfin_progress(job_id)
    now = utc_now()
    connection.last_status = "running"
    connection.last_error = None
    connection.last_sync_started_at = now
    db.commit()
    update_jellyfin_progress("connecting")
    library_count = 0
    seen_items: set[str] = set()
    seen_users: set[str] = set()

    try:
        jellyfin_client = JellyfinClient(
            connection.base_url,
            api_key,
            cancellation_check=_raise_if_sync_cancelled,
        )
        client_context = jellyfin_client if hasattr(jellyfin_client, "__enter__") else nullcontext(jellyfin_client)
        with client_context as client:
            system_info = client.get_system_info()
            _raise_if_sync_cancelled()
            connection = get_or_create_jellyfin_connection(db)
            connection.server_name = system_info.get("ServerName")
            connection.server_version = system_info.get("Version")

            update_jellyfin_progress("users")
            remote_users = client.get_users()
            stored_users = {
                user.jellyfin_user_id: user for user in db.scalars(select(JellyfinUser))
            }
            for payload in remote_users:
                _raise_if_sync_cancelled()
                user_id = str(payload.get("Id") or "").strip()
                if not user_id:
                    continue
                seen_users.add(user_id)
                user = stored_users.get(user_id)
                if user is None:
                    user = JellyfinUser(
                        jellyfin_user_id=user_id,
                        name=str(payload.get("Name") or "Unknown user"),
                    )
                    db.add(user)
                    stored_users[user_id] = user
                else:
                    user.name = str(payload.get("Name") or user.name)
            for user_id, user in stored_users.items():
                if user_id not in seen_users:
                    db.delete(user)
            db.flush()

            update_jellyfin_progress("libraries")
            folders = client.get_virtual_folders()
            _raise_if_sync_cancelled()
            libraries_by_remote_id = _sync_libraries(db, folders, now)
            library_count = len(libraries_by_remote_id)

            # Fetch the canonical catalog once. Pages are validated and persisted
            # immediately; cleanup happens only after every page completed.
            update_jellyfin_progress("items")

            def report_catalog_progress(current: int, total: int | None) -> None:
                _raise_if_sync_cancelled()
                update_jellyfin_progress("items", current=current, total=total)

            for page in _iter_item_pages(client, progress_callback=report_catalog_progress):
                remote_ids = {
                    str(payload.get("Id") or "").strip()
                    for payload in page.items
                    if str(payload.get("Id") or "").strip()
                }
                existing_items = {
                    item.jellyfin_item_id: item
                    for item in db.scalars(
                        select(JellyfinItem).where(JellyfinItem.jellyfin_item_id.in_(remote_ids))
                    )
                } if remote_ids else {}
                for payload in page.items:
                    _raise_if_sync_cancelled()
                    item = _upsert_item(
                        db,
                        payload,
                        folders,
                        libraries_by_remote_id,
                        existing_items,
                        now,
                    )
                    if item is not None:
                        seen_items.add(item.jellyfin_item_id)
                db.flush()
                update_jellyfin_progress(
                    "saving",
                    current=min(page.start_index + len(page.items), page.total_record_count),
                    total=page.total_record_count,
                )

            enabled_users = [
                user
                for user in stored_users.values()
                if user.enabled_for_sync and user.jellyfin_user_id in seen_users
            ]
            disabled_user_ids = [
                user.jellyfin_user_id
                for user in stored_users.values()
                if not user.enabled_for_sync
            ]
            if disabled_user_ids:
                db.execute(
                    delete(JellyfinUserItemData).where(
                        JellyfinUserItemData.jellyfin_user_id.in_(disabled_user_ids)
                    )
                )

            for user in enabled_users:
                _raise_if_sync_cancelled()
                seen_user_item_ids: set[int] = set()
                detail = user.name

                def report_user_progress(current: int, total: int | None) -> None:
                    _raise_if_sync_cancelled()
                    update_jellyfin_progress("items", detail=detail, current=current, total=total)

                for page in _iter_item_pages(
                    client,
                    user_id=user.jellyfin_user_id,
                    user_data_only=True,
                    progress_callback=report_user_progress,
                ):
                    remote_ids = {
                        str(payload.get("Id") or "").strip()
                        for payload in page.items
                        if str(payload.get("Id") or "").strip()
                    }
                    page_items = {
                        item.jellyfin_item_id: item
                        for item in db.scalars(
                            select(JellyfinItem).where(JellyfinItem.jellyfin_item_id.in_(remote_ids))
                        )
                    } if remote_ids else {}
                    item_ids = [item.id for item in page_items.values()]
                    existing_user_data = {
                        row.jellyfin_item_id: row
                        for row in db.scalars(
                            select(JellyfinUserItemData).where(
                                JellyfinUserItemData.jellyfin_user_id == user.jellyfin_user_id,
                                JellyfinUserItemData.jellyfin_item_id.in_(item_ids),
                            )
                        )
                    } if item_ids else {}
                    for payload in page.items:
                        item = page_items.get(str(payload.get("Id") or "").strip())
                        if item is None:
                            continue
                        seen_user_item_ids.add(item.id)
                        user_data = payload.get("UserData")
                        if not isinstance(user_data, dict):
                            user_data = {}
                        _upsert_user_data(
                            db, item, user, user_data, existing_user_data, now
                        )
                    db.flush()
                stale_user_data = delete(JellyfinUserItemData).where(
                    JellyfinUserItemData.jellyfin_user_id == user.jellyfin_user_id
                )
                if seen_user_item_ids:
                    stale_user_data = stale_user_data.where(
                        JellyfinUserItemData.jellyfin_item_id.not_in(seen_user_item_ids)
                    )
                db.execute(stale_user_data)
                user.last_synced_at = now
        update_jellyfin_progress("cleanup")
        _raise_if_sync_cancelled()
        stale_ids = list(
            db.scalars(select(JellyfinItem.id).where(JellyfinItem.jellyfin_item_id.not_in(seen_items)))
        ) if seen_items else list(db.scalars(select(JellyfinItem.id)))
        if stale_ids:
            db.execute(delete(JellyfinItem).where(JellyfinItem.id.in_(stale_ids)))
        _raise_if_sync_cancelled()

        update_jellyfin_progress("matching")
        match_summary = recompute_jellyfin_matches(
            db,
            cancellation_check=_raise_if_sync_cancelled,
            commit=False,
        )
        _raise_if_sync_cancelled()
        finished_at = utc_now()
        connection = get_or_create_jellyfin_connection(db)
        connection.last_status = "success"
        connection.last_error = None
        connection.last_sync_finished_at = finished_at
        connection.last_successful_sync_at = finished_at
        db.commit()
        return {
            "status": "success",
            "libraries_synced": library_count,
            "items_synced": len(seen_items),
            "users_synced": len(seen_users),
            **match_summary,
        }
    except JellyfinSyncCancelled:
        db.rollback()
        connection = get_or_create_jellyfin_connection(db)
        connection.last_status = "canceled"
        connection.last_error = None
        connection.last_sync_finished_at = utc_now()
        db.commit()
        return {
            "status": "canceled",
            "libraries_synced": 0,
            "items_synced": 0,
            "users_synced": 0,
            "matches_created": 0,
            "unmatched_items": 0,
        }
    except Exception as exc:
        db.rollback()
        connection = get_or_create_jellyfin_connection(db)
        connection.last_status = "error"
        connection.last_error = str(exc)[:2048]
        connection.last_sync_finished_at = utc_now()
        db.commit()
        raise
    finally:
        clear_jellyfin_progress()
        reset_jellyfin_cancellation()
