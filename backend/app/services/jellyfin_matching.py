from __future__ import annotations

from collections import defaultdict
from pathlib import Path, PurePosixPath
import re
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.entities import (
    JellyfinItem,
    JellyfinLibrary,
    JellyfinMediaMatch,
    JellyfinPathMapping,
    MediaFile,
)

SUPPORTED_ITEM_TYPES = {"movie", "episode", "audio", "audiobook"}


def refresh_jellyfin_mapping_state(db: Session) -> None:
    mappings = list(db.scalars(select(JellyfinPathMapping).where(JellyfinPathMapping.enabled.is_(True))))
    for library in db.scalars(select(JellyfinLibrary)):
        library.mapped_locations, library.mapped_status = map_library_locations(
            list(library.locations or []), mappings
        )
        if library.linked_library_id is not None:
            library.mapped_status = "linked"
    db.commit()


def normalize_jellyfin_path(value: str) -> str:
    candidate = _display_path(value)
    return candidate.casefold()


def _display_path(value: str) -> str:
    candidate = re.sub(r"/+", "/", value.strip().replace("\\", "/"))
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate


def apply_path_mappings(path: str, mappings: list[JellyfinPathMapping]) -> tuple[str, bool]:
    display_path = _display_path(path)
    normalized = display_path.casefold()
    enabled = sorted(
        (mapping for mapping in mappings if mapping.enabled),
        key=lambda mapping: len(normalize_jellyfin_path(mapping.jellyfin_path_prefix)),
        reverse=True,
    )
    for mapping in enabled:
        source_display = _display_path(mapping.jellyfin_path_prefix)
        source = source_display.casefold()
        if normalized != source and not normalized.startswith(f"{source}/"):
            continue
        suffix = display_path[len(source_display) :].lstrip("/")
        target = _display_path(mapping.medialyze_path_prefix)
        return f"{target}/{suffix}" if suffix else target, True
    return display_path, False


def mapped_path_is_accessible(
    mapped_path: str,
    mappings: list[JellyfinPathMapping],
    *,
    accessibility_cache: dict[int, bool] | None = None,
) -> bool:
    normalized = normalize_jellyfin_path(mapped_path)
    for mapping in mappings:
        target = normalize_jellyfin_path(mapping.medialyze_path_prefix)
        if mapping.enabled and (normalized == target or normalized.startswith(f"{target}/")):
            if accessibility_cache is not None and mapping.id is not None:
                if mapping.id not in accessibility_cache:
                    accessibility_cache[mapping.id] = Path(mapping.medialyze_path_prefix).expanduser().exists()
                return accessibility_cache[mapping.id]
            return Path(mapping.medialyze_path_prefix).expanduser().exists()
    return Path(mapped_path).expanduser().exists()


def _absolute_media_path(media_file: MediaFile) -> str:
    root = media_file.library_root.path if media_file.library_root else media_file.library.path
    return normalize_jellyfin_path(str(PurePosixPath(root.replace("\\", "/")) / media_file.relative_path))


def recompute_jellyfin_matches(
    db: Session,
    *,
    media_file_ids: set[int] | None = None,
    cancellation_check: Callable[[], None] | None = None,
    commit: bool = True,
    commit_batch_size: int | None = None,
) -> dict[str, int]:
    mappings = list(db.scalars(select(JellyfinPathMapping).where(JellyfinPathMapping.enabled.is_(True))))
    mapping_accessibility: dict[int, bool] = {}
    claimed_media_file_ids: set[int] = set()
    media_query = select(MediaFile).options(selectinload(MediaFile.library), selectinload(MediaFile.library_root))
    if media_file_ids is not None:
        media_query = media_query.where(MediaFile.id.in_(media_file_ids))
    media_files = list(db.scalars(media_query))

    exact_paths: dict[str, list[MediaFile]] = defaultdict(list)
    filenames: dict[str, list[MediaFile]] = defaultdict(list)
    for media_file in media_files:
        if cancellation_check is not None:
            cancellation_check()
        exact_paths[_absolute_media_path(media_file)].append(media_file)
        filenames[media_file.filename.casefold()].append(media_file)

    if media_file_ids is None:
        db.execute(delete(JellyfinMediaMatch))
        items = list(db.scalars(select(JellyfinItem)))
    else:
        previously_matched_item_ids = set(
            db.scalars(
                select(JellyfinMediaMatch.jellyfin_item_id).where(
                    JellyfinMediaMatch.media_file_id.in_(media_file_ids),
                )
            )
        )
        db.execute(
            delete(JellyfinMediaMatch).where(
                JellyfinMediaMatch.media_file_id.in_(media_file_ids),
            )
        )
        items = []
        for candidate in db.scalars(select(JellyfinItem)):
            mapped_path, _ = apply_path_mappings(candidate.path or "", mappings)
            if candidate.id in previously_matched_item_ids or normalize_jellyfin_path(mapped_path) in exact_paths:
                items.append(candidate)

    if commit and commit_batch_size:
        db.commit()

    created = 0
    unmatched = 0
    for item_index, item in enumerate(items):
        if commit and commit_batch_size and item_index and item_index % commit_batch_size == 0:
            db.commit()
        if cancellation_check is not None:
            cancellation_check()
        item_type = item.item_type.casefold()
        if item_type not in SUPPORTED_ITEM_TYPES:
            item.match_status = "unmatched"
            item.mismatch_reason = "unsupported_item_type"
            unmatched += 1
            continue
        if not item.path:
            item.match_status = "unmatched"
            item.mismatch_reason = "path_unmapped"
            unmatched += 1
            continue

        mapped_path, mapping_applied = apply_path_mappings(item.path, mappings)
        candidates = exact_paths.get(normalize_jellyfin_path(mapped_path), [])
        if not mapping_applied and not candidates:
            item.match_status = "unmatched"
            item.mismatch_reason = "path_unmapped"
            unmatched += 1
            continue
        if mapping_applied and not mapped_path_is_accessible(
            mapped_path,
            mappings,
            accessibility_cache=mapping_accessibility,
        ):
            item.match_status = "unmatched"
            item.mismatch_reason = "path_not_accessible"
            unmatched += 1
            continue
        if len(candidates) > 1:
            item.match_status = "ambiguous"
            item.mismatch_reason = "ambiguous"
            unmatched += 1
            continue
        if len(candidates) == 1:
            if candidates[0].id in claimed_media_file_ids:
                item.match_status = "ambiguous"
                item.mismatch_reason = "media_file_already_matched"
                unmatched += 1
                continue
            match = JellyfinMediaMatch(
                media_file_id=candidates[0].id,
                jellyfin_item_id=item.id,
                match_method="path",
                confidence=1.0,
                status="matched",
            )
            db.add(match)
            item.match_status = "matched"
            item.mismatch_reason = None
            claimed_media_file_ids.add(candidates[0].id)
            created += 1
            continue

        raw = item.raw_limited_payload or {}
        size = raw.get("Size")
        runtime_ticks = raw.get("RunTimeTicks")
        suggestions = []
        for candidate in filenames.get(Path(item.path).name.casefold(), []):
            if size is not None and int(size) != candidate.size_bytes:
                continue
            if runtime_ticks and candidate.duration_seconds is not None:
                if abs(float(runtime_ticks) / 10_000_000 - candidate.duration_seconds) > 3:
                    continue
            suggestions.append(candidate)
        if len(suggestions) > 1:
            item.mismatch_reason = "ambiguous"
        else:
            item.mismatch_reason = "no_local_file"
        item.match_status = "ambiguous" if len(suggestions) > 1 else "unmatched"
        unmatched += 1

    if commit:
        db.commit()
    else:
        db.flush()
    return {"matches_created": created, "unmatched_items": unmatched}


def map_library_locations(
    locations: list[str], mappings: list[JellyfinPathMapping]
) -> tuple[list[str], str]:
    mapped: list[str] = []
    any_unmapped = False
    any_inaccessible = False
    for location in locations:
        target, applied = apply_path_mappings(location, mappings)
        if not applied:
            any_unmapped = True
            continue
        mapped.append(target)
        if not mapped_path_is_accessible(target, mappings):
            any_inaccessible = True
    if any_inaccessible:
        return mapped, "path_not_accessible"
    if any_unmapped or not mapped:
        return mapped, "path_unmapped"
    return mapped, "accessible"
