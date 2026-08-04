from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.models.entities import ConnectorItem, ConnectorMediaMatch, MediaFile
from backend.app.services.connector_pathing import resolve_connector_item_path
from backend.app.services.stats_cache import stats_cache


SUPPORTED_ITEM_TYPES = {"movie", "episode", "audio", "audiobook"}


def recompute_connector_matches(
    db: Session,
    *,
    connection_id: int | None = None,
    media_file_ids: set[int] | None = None,
    cancellation_check: Callable[[], None] | None = None,
    commit: bool = True,
) -> dict[str, int]:
    item_query = select(ConnectorItem)
    accessibility_cache: dict[int, bool] = {}
    if connection_id is not None:
        item_query = item_query.where(ConnectorItem.connection_id == connection_id)

    if media_file_ids is not None:
        affected_item_ids = set(
            db.scalars(
                select(ConnectorMediaMatch.connector_item_id).where(
                    ConnectorMediaMatch.media_file_id.in_(media_file_ids),
                    ConnectorMediaMatch.match_method != "manual",
                )
            )
        )
        root_and_paths = list(
            db.execute(
                select(MediaFile.library_root_id, MediaFile.relative_path).where(
                    MediaFile.id.in_(media_file_ids)
                )
            )
        )
        changed_paths: dict[int, tuple[set[str], set[str]]] = defaultdict(
            lambda: (set(), set())
        )
        for root_id, relative_path in root_and_paths:
            if root_id is None:
                continue
            sensitive, insensitive = changed_paths[root_id]
            sensitive.add(relative_path)
            insensitive.add(relative_path.casefold())
        for item in db.scalars(item_query).all():
            resolution = resolve_connector_item_path(
                db,
                item,
                accessibility_cache=accessibility_cache,
            )
            locator = resolution.locator
            if locator is None or locator.library_root_id not in changed_paths:
                continue
            sensitive, insensitive = changed_paths[locator.library_root_id]
            if (
                locator.relative_path in sensitive
                if locator.case_mode == "sensitive"
                else locator.relative_path.casefold() in insensitive
            ):
                affected_item_ids.add(item.id)
        item_query = item_query.where(ConnectorItem.id.in_(affected_item_ids))

    items = list(db.scalars(item_query.order_by(ConnectorItem.id)))
    item_ids = {item.id for item in items}
    manual_matches = {
        match.connector_item_id: match
        for match in db.scalars(
            select(ConnectorMediaMatch).where(
                ConnectorMediaMatch.connector_item_id.in_(item_ids),
                ConnectorMediaMatch.match_method == "manual",
            )
        )
    }
    if item_ids:
        db.execute(
            delete(ConnectorMediaMatch).where(
                ConnectorMediaMatch.connector_item_id.in_(item_ids),
                ConnectorMediaMatch.match_method != "manual",
            )
        )

    result = defaultdict(int)
    for item in items:
        if cancellation_check:
            cancellation_check()
        if item.id in manual_matches:
            item.match_status = "matched"
            item.mismatch_reason = None
            result["manual_preserved"] += 1
            continue
        item.suggested_media_file_id = None
        if item.match_status == "ignored":
            result["ignored"] += 1
            continue
        if item.item_type.casefold() not in SUPPORTED_ITEM_TYPES:
            item.match_status = "unsupported_item_type"
            item.mismatch_reason = "unsupported_item_type"
            result["unsupported_item_type"] += 1
            continue

        resolution = resolve_connector_item_path(
            db,
            item,
            accessibility_cache=accessibility_cache,
        )
        if resolution.locator is None:
            item.match_status = resolution.status
            item.mismatch_reason = resolution.reason
            result[resolution.status] += 1
            continue
        locator = resolution.locator
        candidate_query = select(MediaFile).where(
            MediaFile.library_root_id == locator.library_root_id
        )
        if locator.case_mode == "insensitive":
            candidate_query = candidate_query.where(
                func.lower(MediaFile.relative_path) == locator.relative_path.casefold()
            )
        else:
            candidate_query = candidate_query.where(MediaFile.relative_path == locator.relative_path)
        candidates = list(db.scalars(candidate_query))
        if len(candidates) > 1:
            item.match_status = "ambiguous_file"
            item.mismatch_reason = "ambiguous_file"
            result["ambiguous_file"] += 1
            continue
        if len(candidates) == 1:
            db.add(
                ConnectorMediaMatch(
                    connector_item_id=item.id,
                    media_file_id=candidates[0].id,
                    binding_id=locator.binding_id,
                    match_method="path",
                    confidence=1.0,
                    status="matched",
                )
            )
            item.match_status = "matched"
            item.mismatch_reason = None
            result["matched"] += 1
            continue

        filename = PurePosixPath(locator.relative_path).name.casefold()
        suggestions = list(
            db.scalars(
                select(MediaFile).where(func.lower(MediaFile.filename) == filename)
            )
        )
        suggestions = [
            candidate
            for candidate in suggestions
            if (item.size_bytes is None or candidate.size_bytes == item.size_bytes)
            and (
                item.duration_seconds is None
                or candidate.duration_seconds is None
                or abs(candidate.duration_seconds - item.duration_seconds) <= 3
            )
        ]
        if len(suggestions) == 1:
            item.suggested_media_file_id = suggestions[0].id
        item.match_status = "ambiguous_file" if len(suggestions) > 1 else "no_local_file"
        item.mismatch_reason = "suggested_metadata_match" if len(suggestions) == 1 else item.match_status
        result[item.match_status] += 1

    if commit:
        db.commit()
        stats_cache.invalidate(str(id(db.get_bind())))
    else:
        db.flush()
    return dict(result)


def compare_legacy_jellyfin_matches(db: Session, connection_id: int) -> dict[str, int]:
    from backend.app.models.entities import JellyfinItem, JellyfinMediaMatch

    legacy = dict(
        db.execute(
            select(JellyfinItem.jellyfin_item_id, JellyfinMediaMatch.media_file_id)
            .join(JellyfinMediaMatch, JellyfinMediaMatch.jellyfin_item_id == JellyfinItem.id)
        ).all()
    )
    generic = dict(
        db.execute(
            select(ConnectorItem.remote_id, ConnectorMediaMatch.media_file_id)
            .join(
                ConnectorMediaMatch,
                ConnectorMediaMatch.connector_item_id == ConnectorItem.id,
            )
            .where(ConnectorItem.connection_id == connection_id)
        ).all()
    )
    counters = defaultdict(int)
    for remote_id in set(legacy) | set(generic):
        old = legacy.get(remote_id)
        new = generic.get(remote_id)
        if old is not None and new == old:
            counters["same_match"] += 1
        elif old is not None and new is None:
            counters["old_only"] += 1
        elif old is None and new is not None:
            counters["new_only"] += 1
        elif old is not None and new is not None:
            counters["different_media_file"] += 1
    counters["ambiguous"] = db.scalar(
        select(func.count()).select_from(ConnectorItem).where(
            ConnectorItem.connection_id == connection_id,
            ConnectorItem.match_status.in_(["ambiguous_binding", "ambiguous_file"]),
        )
    ) or 0
    counters["unmapped"] = db.scalar(
        select(func.count()).select_from(ConnectorItem).where(
            ConnectorItem.connection_id == connection_id,
            ConnectorItem.match_status == "unmapped",
        )
    ) or 0
    return dict(counters)
