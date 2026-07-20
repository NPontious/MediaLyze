from __future__ import annotations

from collections import Counter, defaultdict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    JellyfinItem,
    JellyfinLibrary,
    JellyfinMediaMatch,
    JellyfinUser,
    JellyfinUserItemData,
)
from backend.app.schemas.jellyfin import (
    JellyfinCatalogSummaryRead,
    JellyfinDistributionRead,
    JellyfinLibraryItemPageRead,
    JellyfinLibraryItemRead,
    JellyfinLibraryOverviewRead,
    JellyfinLibraryRead,
    JellyfinUserRead,
)

TICKS_PER_SECOND = 10_000_000


def item_size(item: JellyfinItem) -> int | None:
    value = (item.raw_limited_payload or {}).get("Size")
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def item_duration(item: JellyfinItem) -> float | None:
    value = (item.raw_limited_payload or {}).get("RunTimeTicks")
    return float(value) / TICKS_PER_SECOND if isinstance(value, (int, float)) and value >= 0 else None


def library_read(db: Session, library: JellyfinLibrary) -> JellyfinLibraryRead:
    linked_name = None
    if library.linked_library_id is not None:
        from backend.app.models.entities import Library

        linked = db.get(Library, library.linked_library_id)
        linked_name = linked.name if linked else None
    item_count = db.scalar(
        select(func.count(JellyfinItem.id)).where(
            JellyfinItem.library_name == library.name
        )
    ) or 0
    return JellyfinLibraryRead(
        id=library.id,
        name=library.name,
        collection_type=library.collection_type,
        locations=list(library.locations or []),
        mapped_locations=list(library.mapped_locations or []),
        mapped_status=library.mapped_status,
        linked_library_id=library.linked_library_id,
        linked_library_name=linked_name,
        link_method=library.link_method,
        can_create_medialyze_library=(
            library.mapped_status == "accessible" and bool(library.mapped_locations)
        ),
        data_scope="linked" if library.linked_library_id is not None else "jellyfin_only",
        item_count=item_count,
        last_synced_at=library.last_synced_at,
    )


def _items(db: Session, library: JellyfinLibrary) -> list[JellyfinItem]:
    return list(
        db.scalars(
            select(JellyfinItem)
            .where(JellyfinItem.library_name == library.name)
            .order_by(JellyfinItem.title.asc())
        )
    )


def _user_data(
    db: Session, item_ids: list[int], user_id: str | None
) -> dict[int, list[JellyfinUserItemData]]:
    if not item_ids:
        return {}
    query = (
        select(JellyfinUserItemData)
        .join(JellyfinUser, JellyfinUser.jellyfin_user_id == JellyfinUserItemData.jellyfin_user_id)
        .where(
            JellyfinUserItemData.jellyfin_item_id.in_(item_ids),
            JellyfinUser.enabled_for_sync.is_(True),
        )
    )
    if user_id:
        query = query.where(JellyfinUserItemData.jellyfin_user_id == user_id)
    result: dict[int, list[JellyfinUserItemData]] = defaultdict(list)
    for row in db.scalars(query):
        result[row.jellyfin_item_id].append(row)
    return result


def _distribution(counter: Counter[str], *, descending_label: bool = False) -> list[JellyfinDistributionRead]:
    pairs = sorted(counter.items(), key=lambda pair: pair[0], reverse=descending_label)
    return [JellyfinDistributionRead(label=label, value=value) for label, value in pairs]


def catalog_summary(db: Session) -> JellyfinCatalogSummaryRead:
    libraries = list(
        db.scalars(select(JellyfinLibrary).where(JellyfinLibrary.linked_library_id.is_(None)))
    )
    names = [library.name for library in libraries]
    items = list(db.scalars(select(JellyfinItem).where(JellyfinItem.library_name.in_(names)))) if names else []
    sizes = [value for item in items if (value := item_size(item)) is not None]
    durations = [value for item in items if (value := item_duration(item)) is not None]
    return JellyfinCatalogSummaryRead(
        library_count=len(libraries),
        item_count=len(items),
        known_size_bytes=sum(sizes),
        size_known_count=len(sizes),
        known_duration_seconds=sum(durations),
        duration_known_count=len(durations),
        last_synced_at=max((library.last_synced_at for library in libraries), default=None),
    )


def library_overview(db: Session, library: JellyfinLibrary, user_id: str | None) -> JellyfinLibraryOverviewRead:
    items = _items(db, library)
    data = _user_data(db, [item.id for item in items], user_id)
    sizes = [value for item in items if (value := item_size(item)) is not None]
    durations = [value for item in items if (value := item_duration(item)) is not None]
    dates = [item.date_created for item in items if item.date_created is not None]
    types = Counter(item.item_type for item in items)
    years = Counter(str(item.production_year) for item in items if item.production_year is not None)
    months = Counter(item.date_created.strftime("%Y-%m") for item in items if item.date_created)
    played = sum(1 for item in items if any(row.played for row in data.get(item.id, [])))
    users = list(
        db.scalars(
            select(JellyfinUser)
            .where(JellyfinUser.enabled_for_sync.is_(True))
            .order_by(JellyfinUser.name.asc())
        )
    )
    return JellyfinLibraryOverviewRead(
        library=library_read(db, library),
        item_count=len(items),
        known_size_bytes=sum(sizes),
        size_known_count=len(sizes),
        known_duration_seconds=sum(durations),
        duration_known_count=len(durations),
        earliest_date_created=min(dates, default=None),
        latest_date_created=max(dates, default=None),
        item_type_distribution=_distribution(types),
        production_year_distribution=_distribution(years, descending_label=True),
        added_month_distribution=_distribution(months),
        playback_distribution=[
            JellyfinDistributionRead(label="played", value=played),
            JellyfinDistributionRead(label="unplayed", value=len(items) - played),
        ],
        users=[JellyfinUserRead.model_validate(user) for user in users],
    )


def library_items(
    db: Session,
    library: JellyfinLibrary,
    *,
    offset: int,
    limit: int,
    search: str | None,
    item_type: str | None,
    production_year: int | None,
    played: bool | None,
    user_id: str | None,
    sort_key: str,
    sort_direction: str,
) -> JellyfinLibraryItemPageRead:
    items = _items(db, library)
    data = _user_data(db, [item.id for item in items], user_id)
    matches = {
        match.jellyfin_item_id: match.media_file_id
        for match in db.scalars(
            select(JellyfinMediaMatch).where(JellyfinMediaMatch.jellyfin_item_id.in_([item.id for item in items]))
        )
    } if items else {}
    query = (search or "").strip().casefold()
    rows: list[JellyfinLibraryItemRead] = []
    for item in items:
        user_rows = data.get(item.id, [])
        row_played = any(row.played for row in user_rows)
        if query and query not in " ".join(filter(None, [item.title, item.original_title, item.series_name, item.season_name, item.path])).casefold():
            continue
        if item_type and item.item_type.casefold() != item_type.casefold():
            continue
        if production_year is not None and item.production_year != production_year:
            continue
        if played is not None and row_played != played:
            continue
        rows.append(JellyfinLibraryItemRead(
            id=item.id,
            jellyfin_item_id=item.jellyfin_item_id,
            title=item.title,
            original_title=item.original_title,
            item_type=item.item_type,
            series_name=item.series_name,
            season_name=item.season_name,
            index_number=item.index_number,
            parent_index_number=item.parent_index_number,
            date_created=item.date_created,
            premiere_date=item.premiere_date,
            production_year=item.production_year,
            size_bytes=item_size(item),
            duration_seconds=item_duration(item),
            has_primary_image=bool((item.image_tags or {}).get("Primary")),
            play_count=sum(row.play_count for row in user_rows),
            played=row_played,
            played_user_count=sum(1 for row in user_rows if row.played),
            favorite_user_count=sum(1 for row in user_rows if row.is_favorite),
            match_status=item.match_status,
            media_file_id=matches.get(item.id),
        ))
    sorters = {
        "title": lambda row: row.title.casefold(),
        "year": lambda row: row.production_year or -1,
        "duration": lambda row: row.duration_seconds or -1,
        "size": lambda row: row.size_bytes or -1,
        "play_count": lambda row: row.play_count,
    }
    reverse = sort_direction == "desc"
    if sort_key == "added":
        rows.sort(key=lambda row: row.date_created.timestamp() if row.date_created else -1, reverse=reverse)
    else:
        rows.sort(key=sorters[sort_key], reverse=reverse)
    total = len(rows)
    return JellyfinLibraryItemPageRead(items=rows[offset:offset + limit], total=total, offset=offset, limit=limit)
