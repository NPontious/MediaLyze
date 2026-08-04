from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    ConnectorConnection,
    ConnectorItem,
    ConnectorLibrary,
    ConnectorLibraryLink,
    ConnectorLibraryLocation,
    ConnectorMediaMatch,
    ConnectorRootBinding,
    Library,
    LibraryRoot,
    MediaFile,
    JellyfinConnection,
)
from backend.app.schemas.connectors import (
    ConnectorBindingBatchUpdate,
    ConnectorConnectionCreate,
    ConnectorConnectionRead,
    ConnectorConnectionUpdate,
    ConnectorLibraryRead,
    ConnectorLocationRead,
)
from backend.app.services.connector_credentials import (
    read_connector_secret,
    write_connector_secret,
)
from backend.app.services.connector_matching import recompute_connector_matches
from backend.app.services.connector_pathing import (
    binding_source_key,
    normalize_connector_path,
    normalize_target_subpath,
)
from backend.app.services.connector_registry import connector_registry
from backend.app.services.stats_cache import stats_cache


def serialize_connector_connection(db: Session, connection: ConnectorConnection) -> ConnectorConnectionRead:
    payload = ConnectorConnectionRead.model_validate(connection)
    payload.has_secret = bool(read_connector_secret(db, connection.id))
    return payload


def mirror_legacy_jellyfin_connection(
    db: Session,
    legacy: JellyfinConnection,
    secret: str,
) -> ConnectorConnection:
    connection = db.scalar(
        select(ConnectorConnection).where(
            ConnectorConnection.provider == "jellyfin",
            ConnectorConnection.name == "Jellyfin",
        )
    )
    if connection is None:
        connection = ConnectorConnection(provider="jellyfin", name="Jellyfin")
        db.add(connection)
        db.flush()
    connection.base_url = legacy.base_url
    connection.enabled = legacy.enabled
    connection.sync_interval_minutes = legacy.sync_interval_minutes
    connection.server_name = legacy.server_name
    connection.server_version = legacy.server_version
    connection.last_status = legacy.last_status
    connection.last_error = legacy.last_error
    connection.last_sync_started_at = legacy.last_sync_started_at
    connection.last_sync_finished_at = legacy.last_sync_finished_at
    connection.last_successful_sync_at = legacy.last_successful_sync_at
    connection.capabilities = {
        "users": True,
        "user_states": True,
        "playback_events": True,
        "images": True,
    }
    if secret:
        write_connector_secret(db, connection.id, secret)
    db.commit()
    return connection


def create_connector_connection(
    db: Session,
    payload: ConnectorConnectionCreate,
) -> ConnectorConnection:
    provider = payload.provider.strip().casefold()
    if provider not in connector_registry.providers():
        raise ValueError(f"Unsupported connector provider: {payload.provider}")
    connection = ConnectorConnection(
        provider=provider,
        name=payload.name.strip(),
        base_url=payload.base_url.strip(),
        config=payload.config,
        capabilities={},
        enabled=payload.enabled,
        sync_interval_minutes=payload.sync_interval_minutes,
    )
    db.add(connection)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("A connector with this provider and name already exists") from exc
    if payload.secret:
        write_connector_secret(db, connection.id, payload.secret)
    db.commit()
    db.refresh(connection)
    return connection


def update_connector_connection(
    db: Session,
    connection: ConnectorConnection,
    payload: ConnectorConnectionUpdate,
) -> ConnectorConnection:
    for field in ("name", "base_url", "config", "enabled", "sync_interval_minutes"):
        value = getattr(payload, field)
        if value is not None:
            setattr(connection, field, value.strip() if isinstance(value, str) else value)
    if payload.secret is not None:
        write_connector_secret(db, connection.id, payload.secret)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("A connector with this provider and name already exists") from exc
    db.refresh(connection)
    return connection


def delete_connector_connection(db: Session, connection: ConnectorConnection) -> None:
    connection_id = connection.id
    db.delete(connection)
    db.commit()
    stats_cache.invalidate(str(id(db.get_bind())))
    _refresh_preferred_connections(db)
    db.commit()


def list_connector_libraries(db: Session, connection_id: int) -> list[ConnectorLibraryRead]:
    libraries = list(
        db.scalars(
            select(ConnectorLibrary)
            .where(ConnectorLibrary.connection_id == connection_id)
            .order_by(func.lower(ConnectorLibrary.name), ConnectorLibrary.id)
        )
    )
    location_rows = db.scalars(
        select(ConnectorLibraryLocation).where(
            ConnectorLibraryLocation.connector_library_id.in_([library.id for library in libraries])
        )
    ).all() if libraries else []
    links = db.execute(
        select(ConnectorLibraryLink.connector_library_id, ConnectorLibraryLink.library_id).where(
            ConnectorLibraryLink.connector_library_id.in_([library.id for library in libraries])
        )
    ).all() if libraries else []
    locations_by_library: dict[int, list[ConnectorLocationRead]] = defaultdict(list)
    for location in location_rows:
        locations_by_library[location.connector_library_id].append(
            ConnectorLocationRead.model_validate(location)
        )
    links_by_library: dict[int, list[int]] = defaultdict(list)
    for connector_library_id, library_id in links:
        links_by_library[connector_library_id].append(library_id)
    return [
        ConnectorLibraryRead(
            **ConnectorLibraryRead.model_validate(library).model_dump(exclude={"locations", "linked_library_ids"}),
            locations=locations_by_library[library.id],
            linked_library_ids=sorted(links_by_library[library.id]),
        )
        for library in libraries
    ]


def replace_connector_bindings(
    db: Session,
    connection_id: int,
    payload: ConnectorBindingBatchUpdate,
) -> list[ConnectorRootBinding]:
    connection = db.get(ConnectorConnection, connection_id)
    if connection is None:
        raise LookupError("Connector connection not found")
    location_ids = {binding.location_id for binding in payload.bindings}
    locations = {
        location.id: location
        for location in db.scalars(
            select(ConnectorLibraryLocation)
            .join(ConnectorLibrary)
            .where(
                ConnectorLibrary.connection_id == connection_id,
                ConnectorLibraryLocation.id.in_(location_ids),
            )
        )
    } if location_ids else {}
    if set(locations) != location_ids:
        raise ValueError("Every binding location must belong to the connector connection")
    root_ids = {binding.library_root_id for binding in payload.bindings}
    roots = {
        root.id: root
        for root in db.scalars(select(LibraryRoot).where(LibraryRoot.id.in_(root_ids)))
    } if root_ids else {}
    if set(roots) != root_ids:
        raise ValueError("Every binding must reference an existing MediaLyze root")

    normalized_rows: list[dict] = []
    tie_keys: set[tuple[int, str, int]] = set()
    for binding in payload.bindings:
        source = normalize_connector_path(binding.source_prefix, case_mode=binding.case_mode)
        location = normalize_connector_path(
            locations[binding.location_id].remote_path,
            case_mode=binding.case_mode,
        )
        if source.key != location.key and not source.key.startswith(f"{location.key}/"):
            raise ValueError("Binding source prefixes must be inside their external library location")
        target_subpath = normalize_target_subpath(binding.target_subpath)
        tie_key = (binding.location_id, source.key, binding.priority)
        if binding.active and tie_key in tie_keys:
            raise ValueError("Equivalent active binding rules are not allowed")
        tie_keys.add(tie_key)
        normalized_rows.append(
            {
                "id": binding.id,
                "location_id": binding.location_id,
                "library_root_id": binding.library_root_id,
                "source_prefix": source.display,
                "normalized_source_prefix": binding_source_key(
                    binding.source_prefix, binding.case_mode
                ),
                "target_subpath": target_subpath,
                "case_mode": binding.case_mode,
                "priority": binding.priority,
                "active": binding.active,
            }
        )

    existing_ids = set(
        db.scalars(
            select(ConnectorRootBinding.id)
            .join(ConnectorLibraryLocation)
            .join(ConnectorLibrary)
            .where(ConnectorLibrary.connection_id == connection_id)
        )
    )
    supplied_ids = {row["id"] for row in normalized_rows if row["id"] is not None}
    if not supplied_ids.issubset(existing_ids):
        raise ValueError("A binding id does not belong to this connector connection")
    db.execute(delete(ConnectorRootBinding).where(ConnectorRootBinding.id.in_(existing_ids - supplied_ids)))
    result: list[ConnectorRootBinding] = []
    for row in normalized_rows:
        binding_id = row.pop("id")
        model = db.get(ConnectorRootBinding, binding_id) if binding_id else None
        if model is None:
            model = ConnectorRootBinding(**row)
            db.add(model)
        else:
            for key, value in row.items():
                setattr(model, key, value)
        result.append(model)
    db.flush()

    # Root bindings imply logical links, but do not remove manual/imported links.
    derived_pairs = {
        (locations[row["location_id"]].connector_library_id, roots[row["library_root_id"]].library_id)
        for row in normalized_rows
        if row["active"]
    }
    db.execute(
        delete(ConnectorLibraryLink).where(
            ConnectorLibraryLink.link_method == "derived",
            ConnectorLibraryLink.connector_library_id.in_(
                select(ConnectorLibrary.id).where(ConnectorLibrary.connection_id == connection_id)
            ),
        )
    )
    for connector_library_id, library_id in derived_pairs:
        exists = db.scalar(
            select(ConnectorLibraryLink.id).where(
                ConnectorLibraryLink.connector_library_id == connector_library_id,
                ConnectorLibraryLink.library_id == library_id,
            )
        )
        if exists is None:
            db.add(
                ConnectorLibraryLink(
                    connector_library_id=connector_library_id,
                    library_id=library_id,
                    link_method="derived",
                )
            )
    db.commit()
    _refresh_preferred_connections(db)
    db.commit()
    recompute_connector_matches(db, connection_id=connection_id)
    return result


def _refresh_preferred_connections(db: Session) -> None:
    libraries = list(db.scalars(select(Library)))
    for library in libraries:
        connection_ids = set(
            db.scalars(
                select(ConnectorLibrary.connection_id)
                .join(
                    ConnectorLibraryLink,
                    ConnectorLibraryLink.connector_library_id == ConnectorLibrary.id,
                )
                .where(ConnectorLibraryLink.library_id == library.id)
            )
        )
        if len(connection_ids) == 1:
            library.preferred_connector_connection_id = next(iter(connection_ids))
        elif library.preferred_connector_connection_id not in connection_ids:
            library.preferred_connector_connection_id = None


def set_manual_connector_match(db: Session, item: ConnectorItem, media_file_id: int) -> ConnectorMediaMatch:
    media_file = db.get(MediaFile, media_file_id)
    if media_file is None:
        raise ValueError("Media file not found")
    existing = db.scalar(
        select(ConnectorMediaMatch).where(ConnectorMediaMatch.connector_item_id == item.id)
    )
    if existing is None:
        existing = ConnectorMediaMatch(
            connector_item_id=item.id,
            media_file_id=media_file_id,
            match_method="manual",
            confidence=1.0,
            status="matched",
        )
        db.add(existing)
    else:
        existing.media_file_id = media_file_id
        existing.binding_id = None
        existing.match_method = "manual"
        existing.confidence = 1.0
        existing.status = "matched"
    item.match_status = "matched"
    item.mismatch_reason = None
    db.commit()
    stats_cache.invalidate(str(id(db.get_bind())))
    return existing


def remove_manual_connector_match(db: Session, item: ConnectorItem) -> bool:
    match = db.scalar(
        select(ConnectorMediaMatch).where(ConnectorMediaMatch.connector_item_id == item.id)
    )
    if match is None:
        return False
    db.delete(match)
    item.match_status = "unmapped"
    item.mismatch_reason = "manual_match_removed"
    db.commit()
    recompute_connector_matches(db, connection_id=item.connection_id)
    return True
