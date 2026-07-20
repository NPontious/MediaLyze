from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock


@dataclass(slots=True)
class JellyfinSyncProgress:
    phase: str | None = None
    detail: str | None = None
    current: int = 0
    total: int | None = None


_lock = Lock()
_progress = JellyfinSyncProgress()


def update_jellyfin_progress(
    phase: str | None,
    *,
    detail: str | None = None,
    current: int = 0,
    total: int | None = None,
) -> None:
    with _lock:
        _progress.phase = phase
        _progress.detail = detail
        _progress.current = current
        _progress.total = total


def clear_jellyfin_progress() -> None:
    update_jellyfin_progress(None)


def get_jellyfin_progress() -> dict[str, str | int | None]:
    with _lock:
        return asdict(_progress)
