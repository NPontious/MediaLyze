from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.app.models.entities import (
    JellyfinItem,
    JellyfinMediaMatch,
    Library,
    LibraryRoot,
    MediaFile,
)
from backend.app.schemas.storage_map import (
    LibraryStorageMapRead,
    StorageMapBreadcrumbRead,
    StorageMapNodeRead,
)
from backend.app.services.app_settings import get_app_settings
from backend.app.services.resolution_categories import classify_resolution_category
from backend.app.services.stats_cache import stats_cache


class StorageMapPathError(ValueError):
    pass


@dataclass(slots=True)
class _FolderAggregate:
    name: str
    path: str
    size_bytes: int = 0
    file_count: int = 0
    weighted_quality_total: int = 0
    codec_bytes: Counter[str] = field(default_factory=Counter)
    resolution_bytes: Counter[str] = field(default_factory=Counter)
    resolution_category_bytes: Counter[tuple[str, str]] = field(default_factory=Counter)
    hdr_bytes: Counter[str] = field(default_factory=Counter)

    def add(
        self,
        *,
        size_bytes: int,
        quality_score: int,
        video_codec: str | None,
        resolution: str | None,
        resolution_category: tuple[str, str] | None,
        hdr_type: str | None,
    ) -> None:
        weight = max(size_bytes, 1)
        self.size_bytes += size_bytes
        self.file_count += 1
        self.weighted_quality_total += quality_score * weight
        if video_codec:
            self.codec_bytes[video_codec] += weight
        if resolution:
            self.resolution_bytes[resolution] += weight
        if resolution_category:
            self.resolution_category_bytes[resolution_category] += weight
        if hdr_type:
            self.hdr_bytes[hdr_type] += weight

    def to_read(self) -> StorageMapNodeRead:
        quality = round(self.weighted_quality_total / max(self.size_bytes, 1)) if self.file_count else None
        category = _dominant(self.resolution_category_bytes)
        return StorageMapNodeRead(
            kind="folder",
            name=self.name,
            path=self.path,
            size_bytes=self.size_bytes,
            file_count=self.file_count,
            video_codec=_dominant(self.codec_bytes),
            resolution=_dominant(self.resolution_bytes),
            resolution_category_id=category[0] if category else None,
            resolution_category_label=category[1] if category else None,
            hdr_type=_dominant(self.hdr_bytes),
            quality_score=quality,
        )


def _dominant(counter: Counter):
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _normalize_path(path: str) -> tuple[str, ...]:
    normalized = path.strip().replace("\\", "/").strip("/")
    if not normalized:
        return ()
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise StorageMapPathError("Invalid storage map path")
    return tuple(parts)


def get_library_storage_map(
    db: Session,
    library_id: int,
    *,
    path: str = "",
) -> LibraryStorageMapRead | None:
    library = db.scalar(select(Library).where(Library.id == library_id))
    if library is None:
        return None

    current_parts = _normalize_path(path)
    cache_key = str(id(db.get_bind()))
    normalized_path = "/".join(current_parts)
    return stats_cache.get_or_compute_storage_map(
        cache_key,
        library_id,
        normalized_path,
        lambda: _build_library_storage_map(db, library, current_parts),
    )


def _build_library_storage_map(
    db: Session,
    library: Library,
    current_parts: tuple[str, ...],
) -> LibraryStorageMapRead:
    library_id = library.id
    resolution_categories = get_app_settings(db).resolution_categories
    roots = list(
        db.scalars(
            select(LibraryRoot)
            .where(LibraryRoot.library_id == library_id)
            .order_by(LibraryRoot.id.asc())
        )
    )
    root_count = len(roots)
    show_root_names = root_count > 1
    query = (
        select(
            MediaFile.id,
            MediaFile.library_root_id,
            LibraryRoot.display_name.label("root_name"),
            MediaFile.relative_path,
            MediaFile.filename,
            MediaFile.extension,
            JellyfinItem.title.label("jellyfin_title"),
            MediaFile.size_bytes,
            MediaFile.quality_score,
            MediaFile.primary_video_codec,
            MediaFile.primary_video_width,
            MediaFile.primary_video_height,
            MediaFile.primary_video_hdr_type,
        )
        .outerjoin(LibraryRoot, LibraryRoot.id == MediaFile.library_root_id)
        .outerjoin(
            JellyfinMediaMatch,
            and_(
                JellyfinMediaMatch.media_file_id == MediaFile.id,
                JellyfinMediaMatch.status == "matched",
            ),
        )
        .outerjoin(JellyfinItem, JellyfinItem.id == JellyfinMediaMatch.jellyfin_item_id)
        .where(MediaFile.library_id == library_id)
        .order_by(MediaFile.relative_path.asc())
    )
    if current_parts:
        relative_parts = current_parts
        if show_root_names:
            selected_root = next((root for root in roots if root.display_name == current_parts[0]), None)
            if selected_root is None:
                raise StorageMapPathError("Storage map folder not found")
            query = query.where(MediaFile.library_root_id == selected_root.id)
            relative_parts = current_parts[1:]
        if relative_parts:
            relative_prefix = "/".join(relative_parts)
            escaped_prefix = (
                relative_prefix
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            query = query.where(MediaFile.relative_path.like(f"{escaped_prefix}/%", escape="\\"))
    rows = db.execute(query).all()

    folders: dict[str, _FolderAggregate] = {}
    files: list[StorageMapNodeRead] = []
    matching_file_count = 0
    matching_size_bytes = 0

    for row in rows:
        relative_parts = PurePosixPath(row.relative_path).parts
        display_parts = ((row.root_name,) if show_root_names and row.root_name else ()) + relative_parts
        if display_parts[: len(current_parts)] != current_parts:
            continue
        remaining = display_parts[len(current_parts) :]
        if not remaining:
            continue

        resolution = (
            f"{row.primary_video_width}x{row.primary_video_height}"
            if row.primary_video_width and row.primary_video_height
            else None
        )
        resolution_category = classify_resolution_category(
            row.primary_video_width,
            row.primary_video_height,
            resolution_categories,
        )
        category_pair = (
            (resolution_category.id, resolution_category.label)
            if resolution_category is not None
            else None
        )
        matching_file_count += 1
        matching_size_bytes += row.size_bytes

        child_path = "/".join((*current_parts, remaining[0]))
        if len(remaining) > 1:
            folder = folders.setdefault(
                remaining[0],
                _FolderAggregate(name=remaining[0], path=child_path),
            )
            folder.add(
                size_bytes=row.size_bytes,
                quality_score=row.quality_score,
                video_codec=row.primary_video_codec,
                resolution=resolution,
                resolution_category=category_pair,
                hdr_type=row.primary_video_hdr_type,
            )
            continue

        files.append(
            StorageMapNodeRead(
                kind="file",
                name=row.filename,
                path=child_path,
                size_bytes=row.size_bytes,
                file_count=1,
                file_id=row.id,
                extension=(row.extension or "").lstrip(".").lower() or None,
                jellyfin_title=row.jellyfin_title,
                video_codec=row.primary_video_codec,
                resolution=resolution,
                resolution_category_id=resolution_category.id if resolution_category else None,
                resolution_category_label=resolution_category.label if resolution_category else None,
                hdr_type=row.primary_video_hdr_type,
                quality_score=row.quality_score,
            )
        )

    if current_parts and matching_file_count == 0:
        raise StorageMapPathError("Storage map folder not found")

    items = [folder.to_read() for folder in folders.values()]
    items.extend(files)
    items.sort(key=lambda item: (-item.size_bytes, item.kind != "folder", item.name.lower()))

    breadcrumbs = [StorageMapBreadcrumbRead(name=library.name, path="")]
    breadcrumbs.extend(
        StorageMapBreadcrumbRead(name=part, path="/".join(current_parts[: index + 1]))
        for index, part in enumerate(current_parts)
    )
    return LibraryStorageMapRead(
        library_id=library.id,
        library_name=library.name,
        path="/".join(current_parts),
        total_size_bytes=matching_size_bytes,
        file_count=matching_file_count,
        breadcrumbs=breadcrumbs,
        items=items,
    )
