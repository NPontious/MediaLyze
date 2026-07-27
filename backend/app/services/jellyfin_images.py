from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from backend.app.services.jellyfin_client import JellyfinClient, JellyfinImage, JellyfinResponseError


@dataclass(slots=True)
class _CacheEntry:
    image: JellyfinImage
    created_at: float
    size_bytes: int


class JellyfinImageCache:
    ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

    def __init__(
        self,
        *,
        max_entries: int = 128,
        max_age_seconds: int = 86400,
        max_bytes: int = 64 * 1024 * 1024,
        max_item_bytes: int = 12 * 1024 * 1024,
    ) -> None:
        self.max_entries = max_entries
        self.max_age_seconds = max_age_seconds
        self.max_bytes = max_bytes
        self.max_item_bytes = max_item_bytes
        self._entries: OrderedDict[tuple[str, str, str, str], _CacheEntry] = OrderedDict()
        self._size_bytes = 0
        self._lock = Lock()

    def get(
        self,
        client: JellyfinClient,
        item_id: str,
        image_type: str,
        tag: str | None,
    ) -> JellyfinImage:
        key = (client.base_url.casefold(), item_id, image_type.casefold(), tag or "")
        with self._lock:
            entry = self._entries.get(key)
            if entry and monotonic() - entry.created_at <= self.max_age_seconds:
                self._entries.move_to_end(key)
                return entry.image
            expired = self._entries.pop(key, None)
            if expired:
                self._size_bytes -= expired.size_bytes
        image = client.get_image(item_id, image_type, tag=tag)
        image_size = len(image.content)
        if image.content_type not in self.ALLOWED_CONTENT_TYPES:
            raise JellyfinResponseError(f"Unsupported Jellyfin image content type: {image.content_type}")
        if image_size > self.max_item_bytes:
            raise JellyfinResponseError("Jellyfin image exceeds the cache size limit")
        with self._lock:
            replaced = self._entries.pop(key, None)
            if replaced:
                self._size_bytes -= replaced.size_bytes
            self._entries[key] = _CacheEntry(
                image=image,
                created_at=monotonic(),
                size_bytes=image_size,
            )
            self._size_bytes += image_size
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries or self._size_bytes > self.max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._size_bytes -= evicted.size_bytes
        return image

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size_bytes = 0


JELLYFIN_IMAGE_CACHE = JellyfinImageCache()
