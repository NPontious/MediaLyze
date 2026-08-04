from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    ConnectorItem,
    ConnectorLibrary,
    ConnectorLibraryLocation,
    ConnectorRootBinding,
    LibraryRoot,
)


_SLASH_RUN = re.compile(r"/+")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:/")


@dataclass(frozen=True, slots=True)
class NormalizedConnectorPath:
    display: str
    key: str
    segments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConnectorFileLocator:
    library_root_id: int
    relative_path: str
    binding_id: int
    case_mode: str


@dataclass(frozen=True, slots=True)
class ConnectorPathResolution:
    status: str
    locator: ConnectorFileLocator | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedConnectorBinding:
    id: int
    connection_id: int
    connector_library_id: int
    library_root_id: int
    root_path: str
    source_prefix: str
    source_display: str
    source_key: str
    source_segments: int
    target_subpath: str
    case_mode: str
    priority: int


PreparedBindingCatalog = dict[int, tuple[PreparedConnectorBinding, ...]]


def normalize_connector_path(value: str, *, case_mode: str = "sensitive") -> NormalizedConnectorPath:
    raw = str(value or "").strip().replace("\\", "/")
    unc = raw.startswith("//")
    collapsed = _SLASH_RUN.sub("/", raw)
    if unc:
        collapsed = f"/{collapsed}"
    if len(collapsed) > 1 and not _DRIVE_PATH.fullmatch(collapsed):
        collapsed = collapsed.rstrip("/")
    parts = tuple(part for part in collapsed.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Connector paths must not contain '.' or '..' segments")
    if not collapsed:
        raise ValueError("Connector path must not be empty")
    if case_mode not in {"sensitive", "insensitive"}:
        raise ValueError("case_mode must be 'sensitive' or 'insensitive'")
    key = collapsed.casefold() if case_mode == "insensitive" else collapsed
    return NormalizedConnectorPath(display=collapsed, key=key, segments=parts)


def normalize_target_subpath(value: str) -> str:
    candidate = str(value or "").strip().replace("\\", "/").strip("/")
    if not candidate:
        return ""
    path = PurePosixPath(candidate)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Target subpath must be a safe relative path")
    return path.as_posix()


def _path_suffix(path: str, prefix: str, case_mode: str) -> str | None:
    normalized_path = normalize_connector_path(path, case_mode=case_mode)
    normalized_prefix = normalize_connector_path(prefix, case_mode=case_mode)
    if normalized_path.key == normalized_prefix.key:
        return ""
    boundary = f"{normalized_prefix.key}/"
    if not normalized_path.key.startswith(boundary):
        return None
    # Slicing the display value by the display prefix length preserves the
    # provider's original case even in insensitive mode.
    return normalized_path.display[len(normalized_prefix.display) :].lstrip("/")


def binding_source_key(source_prefix: str, case_mode: str) -> str:
    return normalize_connector_path(source_prefix, case_mode=case_mode).key


def prepare_connector_bindings(
    db: Session,
    *,
    connection_id: int | None = None,
) -> PreparedBindingCatalog:
    query = (
        select(
            ConnectorRootBinding,
            ConnectorLibraryLocation,
            ConnectorLibrary,
            LibraryRoot,
        )
        .join(
            ConnectorLibraryLocation,
            ConnectorLibraryLocation.id == ConnectorRootBinding.location_id,
        )
        .join(
            ConnectorLibrary,
            ConnectorLibrary.id == ConnectorLibraryLocation.connector_library_id,
        )
        .join(LibraryRoot, LibraryRoot.id == ConnectorRootBinding.library_root_id)
        .where(ConnectorRootBinding.active.is_(True))
    )
    if connection_id is not None:
        query = query.where(ConnectorLibrary.connection_id == connection_id)

    grouped: dict[int, list[PreparedConnectorBinding]] = {}
    for binding, location, connector_library, root in db.execute(query):
        try:
            normalized_source = normalize_connector_path(
                binding.source_prefix,
                case_mode=binding.case_mode,
            )
            target_subpath = normalize_target_subpath(binding.target_subpath)
        except ValueError:
            # Invalid rows can only originate from an old database or a manual
            # database edit; treating them as absent keeps matching safe.
            continue
        prepared = PreparedConnectorBinding(
            id=binding.id,
            connection_id=connector_library.connection_id,
            connector_library_id=location.connector_library_id,
            library_root_id=root.id,
            root_path=root.path,
            source_prefix=binding.source_prefix,
            source_display=normalized_source.display,
            source_key=normalized_source.key,
            source_segments=len(normalized_source.segments),
            target_subpath=target_subpath,
            case_mode=binding.case_mode,
            priority=binding.priority,
        )
        grouped.setdefault(location.connector_library_id, []).append(prepared)
    return {
        library_id: tuple(bindings)
        for library_id, bindings in grouped.items()
    }


def _prepared_path_suffix(
    normalized_path: NormalizedConnectorPath,
    binding: PreparedConnectorBinding,
) -> str | None:
    path_key = (
        normalized_path.display.casefold()
        if binding.case_mode == "insensitive"
        else normalized_path.display
    )
    if path_key == binding.source_key:
        return ""
    if not path_key.startswith(f"{binding.source_key}/"):
        return None
    return normalized_path.display[len(binding.source_display) :].lstrip("/")


def resolve_connector_item_path(
    db: Session,
    item: ConnectorItem,
    *,
    accessibility_cache: dict[int, bool] | None = None,
    prepared_bindings: PreparedBindingCatalog | None = None,
) -> ConnectorPathResolution:
    if not item.remote_path:
        return ConnectorPathResolution("unmapped", reason="path_missing")
    if item.connector_library_id is None:
        return ConnectorPathResolution("unmapped", reason="library_unmapped")
    try:
        normalized_remote_path = normalize_connector_path(item.remote_path)
    except ValueError:
        return ConnectorPathResolution("unmapped", reason="invalid_remote_path")

    catalog = prepared_bindings
    if catalog is None:
        catalog = prepare_connector_bindings(db, connection_id=item.connection_id)
    candidates: list[tuple[int, int, PreparedConnectorBinding, str]] = []
    for binding in catalog.get(item.connector_library_id, ()):
        if binding.connection_id != item.connection_id:
            continue
        suffix = _prepared_path_suffix(normalized_remote_path, binding)
        if suffix is not None:
            candidates.append((binding.source_segments, binding.priority, binding, suffix))
    if not candidates:
        return ConnectorPathResolution("unmapped", reason="no_binding")
    best_key = max((specificity, priority) for specificity, priority, *_rest in candidates)
    best = [candidate for candidate in candidates if candidate[:2] == best_key]
    if len(best) != 1:
        return ConnectorPathResolution("ambiguous_binding", reason="equivalent_bindings")

    _specificity, _priority, binding, suffix = best[0]
    cache = accessibility_cache if accessibility_cache is not None else {}
    if binding.id not in cache:
        cache[binding.id] = Path(binding.root_path).expanduser().exists()
    if not cache[binding.id]:
        return ConnectorPathResolution("root_unavailable", reason="root_not_accessible")

    try:
        relative = normalize_target_subpath(
            "/".join(part for part in (binding.target_subpath, suffix) if part)
        )
    except ValueError:
        return ConnectorPathResolution("unmapped", reason="root_escape")
    if not relative:
        return ConnectorPathResolution("no_local_file", reason="path_resolves_to_root")
    return ConnectorPathResolution(
        "resolved",
        locator=ConnectorFileLocator(
            library_root_id=binding.library_root_id,
            relative_path=relative,
            binding_id=binding.id,
            case_mode=binding.case_mode,
        ),
    )
