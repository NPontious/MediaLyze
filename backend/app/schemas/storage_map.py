from typing import Literal

from pydantic import BaseModel, Field


class StorageMapBreadcrumbRead(BaseModel):
    name: str
    path: str


class StorageMapNodeRead(BaseModel):
    kind: Literal["folder", "file"]
    name: str
    path: str
    size_bytes: int
    file_count: int
    file_id: int | None = None
    extension: str | None = None
    jellyfin_title: str | None = None
    video_codec: str | None = None
    resolution: str | None = None
    resolution_category_id: str | None = None
    resolution_category_label: str | None = None
    hdr_type: str | None = None
    quality_score: int | None = None
    container: str | None = None
    duration_seconds: float | None = None
    bitrate: int | None = None
    audio_bitrate: int | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    frame_rate: float | None = None
    bit_depth: int | None = None
    audio_language: str | None = None
    subtitle_status: str | None = None
    subtitle_language: str | None = None
    analysis_status: str | None = None


class LibraryStorageMapRead(BaseModel):
    library_id: int
    library_name: str
    path: str
    total_size_bytes: int
    file_count: int
    breadcrumbs: list[StorageMapBreadcrumbRead] = Field(default_factory=list)
    items: list[StorageMapNodeRead] = Field(default_factory=list)
