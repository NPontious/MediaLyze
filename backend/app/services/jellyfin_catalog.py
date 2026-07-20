from __future__ import annotations

from collections import Counter, defaultdict
from sqlalchemy import case, func, or_, select
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
    if item.size_bytes is not None:
        return item.size_bytes
    value = (item.raw_limited_payload or {}).get("Size")
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def item_duration(item: JellyfinItem) -> float | None:
    if item.duration_seconds is not None:
        return item.duration_seconds
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
            or_(
                JellyfinItem.library_id == library.id,
                (JellyfinItem.library_id.is_(None)) & (JellyfinItem.library_name == library.name),
            )
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
            .where(
                or_(
                    JellyfinItem.library_id == library.id,
                    (JellyfinItem.library_id.is_(None)) & (JellyfinItem.library_name == library.name),
                )
            )
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
    library_ids = [library.id for library in libraries]
    names = [library.name for library in libraries]
    items = list(
        db.scalars(
            select(JellyfinItem).where(
                or_(
                    JellyfinItem.library_id.in_(library_ids),
                    (JellyfinItem.library_id.is_(None)) & JellyfinItem.library_name.in_(names),
                )
            )
        )
    ) if names else []
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
    playback_query = (
        select(
            JellyfinUserItemData.jellyfin_item_id.label("item_id"),
            func.coalesce(func.sum(JellyfinUserItemData.play_count), 0).label("play_count"),
            func.coalesce(func.sum(case((JellyfinUserItemData.played.is_(True), 1), else_=0)), 0).label("played_user_count"),
            func.coalesce(func.sum(case((JellyfinUserItemData.is_favorite.is_(True), 1), else_=0)), 0).label("favorite_user_count"),
        )
        .join(JellyfinUser, JellyfinUser.jellyfin_user_id == JellyfinUserItemData.jellyfin_user_id)
        .where(JellyfinUser.enabled_for_sync.is_(True))
        .group_by(JellyfinUserItemData.jellyfin_item_id)
    )
    if user_id:
        playback_query = playback_query.where(JellyfinUserItemData.jellyfin_user_id == user_id)
    playback = playback_query.subquery()

    play_count_expr = func.coalesce(playback.c.play_count, 0)
    played_count_expr = func.coalesce(playback.c.played_user_count, 0)
    favorite_count_expr = func.coalesce(playback.c.favorite_user_count, 0)
    statement = (
        select(
            JellyfinItem,
            play_count_expr.label("aggregated_play_count"),
            played_count_expr.label("aggregated_played_count"),
            favorite_count_expr.label("aggregated_favorite_count"),
            JellyfinMediaMatch.media_file_id,
        )
        .outerjoin(playback, playback.c.item_id == JellyfinItem.id)
        .outerjoin(
            JellyfinMediaMatch,
            (JellyfinMediaMatch.jellyfin_item_id == JellyfinItem.id)
            & (JellyfinMediaMatch.status == "matched"),
        )
        .where(
            or_(
                JellyfinItem.library_id == library.id,
                (JellyfinItem.library_id.is_(None)) & (JellyfinItem.library_name == library.name),
            )
        )
    )
    query = (search or "").strip().casefold()
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                func.lower(JellyfinItem.title).like(pattern),
                func.lower(func.coalesce(JellyfinItem.original_title, "")).like(pattern),
                func.lower(func.coalesce(JellyfinItem.series_name, "")).like(pattern),
                func.lower(func.coalesce(JellyfinItem.season_name, "")).like(pattern),
                func.lower(func.coalesce(JellyfinItem.path, "")).like(pattern),
            )
        )
    if item_type:
        statement = statement.where(func.lower(JellyfinItem.item_type) == item_type.casefold())
    if production_year is not None:
        statement = statement.where(JellyfinItem.production_year == production_year)
    if played is not None:
        statement = statement.where(played_count_expr > 0 if played else played_count_expr == 0)

    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    sorters = {
        "title": func.lower(JellyfinItem.title),
        "year": func.coalesce(JellyfinItem.production_year, -1),
        "duration": func.coalesce(JellyfinItem.duration_seconds, -1),
        "size": func.coalesce(JellyfinItem.size_bytes, -1),
        "play_count": play_count_expr,
        "added": func.coalesce(JellyfinItem.date_created, "1970-01-01"),
    }
    sort_expression = sorters[sort_key]
    statement = statement.order_by(
        sort_expression.desc() if sort_direction == "desc" else sort_expression.asc(),
        JellyfinItem.id.asc(),
    ).offset(offset).limit(limit)

    rows = [
        JellyfinLibraryItemRead(
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
            play_count=int(aggregated_play_count or 0),
            played=bool(aggregated_played_count),
            played_user_count=int(aggregated_played_count or 0),
            favorite_user_count=int(aggregated_favorite_count or 0),
            match_status=item.match_status,
            media_file_id=media_file_id,
        )
        for item, aggregated_play_count, aggregated_played_count, aggregated_favorite_count, media_file_id in db.execute(statement)
    ]
    return JellyfinLibraryItemPageRead(items=rows, total=total, offset=offset, limit=limit)
