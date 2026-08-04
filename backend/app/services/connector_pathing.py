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


def resolve_connector_item_path(
    db: Session,
    item: ConnectorItem,
    *,
    accessibility_cache: dict[int, bool] | None = None,
) -> ConnectorPathResolution:
    if not item.remote_path:
        return ConnectorPathResolution("unmapped", reason="path_missing")
    if item.connector_library_id is None:
        return ConnectorPathResolution("unmapped", reason="library_unmapped")
    try:
        normalize_connector_path(item.remote_path)
    except ValueError:
        return ConnectorPathResolution("unmapped", reason="invalid_remote_path")

    rows = db.execute(
        select(ConnectorRootBinding, LibraryRoot)
        .join(
            ConnectorLibraryLocation,
            ConnectorLibraryLocation.id == ConnectorRootBinding.location_id,
        )
        .join(
            ConnectorLibrary,
            ConnectorLibrary.id == ConnectorLibraryLocation.connector_library_id,
        )
        .join(LibraryRoot, LibraryRoot.id == ConnectorRootBinding.library_root_id)
        .where(
            ConnectorRootBinding.active.is_(True),
            ConnectorLibraryLocation.connector_library_id == item.connector_library_id,
            ConnectorLibrary.connection_id == item.connection_id,
        )
    ).all()
    candidates: list[tuple[int, int, ConnectorRootBinding, LibraryRoot, str]] = []
    for binding, root in rows:
        try:
            suffix = _path_suffix(item.remote_path, binding.source_prefix, binding.case_mode)
            specificity = len(
                normalize_connector_path(binding.source_prefix, case_mode=binding.case_mode).segments
            )
        except ValueError:
            continue
        if suffix is not None:
            candidates.append((specificity, binding.priority, binding, root, suffix))
    if not candidates:
        return ConnectorPathResolution("unmapped", reason="no_binding")
    best_key = max((specificity, priority) for specificity, priority, *_rest in candidates)
    best = [candidate for candidate in candidates if candidate[:2] == best_key]
    if len(best) != 1:
        return ConnectorPathResolution("ambiguous_binding", reason="equivalent_bindings")

    _specificity, _priority, binding, root, suffix = best[0]
    cache = accessibility_cache if accessibility_cache is not None else {}
    if binding.id not in cache:
        cache[binding.id] = Path(root.path).expanduser().exists()
    if not cache[binding.id]:
        return ConnectorPathResolution("root_unavailable", reason="root_not_accessible")

    try:
        target = normalize_target_subpath(binding.target_subpath)
        relative = normalize_target_subpath("/".join(part for part in (target, suffix) if part))
    except ValueError:
        return ConnectorPathResolution("unmapped", reason="root_escape")
    if not relative:
        return ConnectorPathResolution("no_local_file", reason="path_resolves_to_root")
    return ConnectorPathResolution(
        "resolved",
        locator=ConnectorFileLocator(
            library_root_id=root.id,
            relative_path=relative,
            binding_id=binding.id,
            case_mode=binding.case_mode,
        ),
    )
