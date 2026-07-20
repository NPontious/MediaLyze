from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from backend.app.services.jellyfin_client import JellyfinClient, JellyfinImage


@dataclass(slots=True)
class _CacheEntry:
    image: JellyfinImage
    created_at: float


class JellyfinImageCache:
    def __init__(self, *, max_entries: int = 128, max_age_seconds: int = 86400) -> None:
        self.max_entries = max_entries
        self.max_age_seconds = max_age_seconds
        self._entries: OrderedDict[tuple[str, str, str], _CacheEntry] = OrderedDict()
        self._lock = Lock()

    def get(
        self,
        client: JellyfinClient,
        item_id: str,
        image_type: str,
        tag: str | None,
    ) -> JellyfinImage:
        key = (item_id, image_type.casefold(), tag or "")
        with self._lock:
            entry = self._entries.get(key)
            if entry and monotonic() - entry.created_at <= self.max_age_seconds:
                self._entries.move_to_end(key)
                return entry.image
            self._entries.pop(key, None)
        image = client.get_image(item_id, image_type, tag=tag)
        with self._lock:
            self._entries[key] = _CacheEntry(image=image, created_at=monotonic())
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return image

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


JELLYFIN_IMAGE_CACHE = JellyfinImageCache()
