from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Event, Lock
from time import monotonic
from collections.abc import Callable


@dataclass(slots=True)
class JellyfinSyncProgress:
    job_id: int | None = None
    phase: str | None = None
    detail: str | None = None
    current: int = 0
    total: int | None = None
    cancellation_requested: bool = False


_lock = Lock()
_progress = JellyfinSyncProgress()
_cancel_event = Event()
_persist_callback: Callable[..., None] | None = None
_last_persisted_at = 0.0
_last_persisted_phase: str | None = None


def begin_jellyfin_progress(
    job_id: int | None,
    persist_callback: Callable[..., None] | None = None,
) -> None:
    global _persist_callback, _last_persisted_at, _last_persisted_phase
    with _lock:
        _progress.job_id = job_id
        _progress.phase = None
        _progress.detail = None
        _progress.current = 0
        _progress.total = None
        _progress.cancellation_requested = _cancel_event.is_set()
        _persist_callback = persist_callback
        _last_persisted_at = 0.0
        _last_persisted_phase = None


def update_jellyfin_progress(
    phase: str | None,
    *,
    detail: str | None = None,
    current: int = 0,
    total: int | None = None,
) -> None:
    global _last_persisted_at, _last_persisted_phase
    callback = None
    with _lock:
        _progress.phase = phase
        _progress.detail = detail
        _progress.current = current
        _progress.total = total
        now = monotonic()
        if _persist_callback is not None and (
            phase != _last_persisted_phase or now - _last_persisted_at >= 0.5
        ):
            callback = _persist_callback
            _last_persisted_at = now
            _last_persisted_phase = phase
    if callback is not None:
        callback(phase=phase, detail=detail, current=current, total=total)


def clear_jellyfin_progress() -> None:
    global _persist_callback, _last_persisted_at, _last_persisted_phase
    with _lock:
        _progress.job_id = None
        _progress.phase = None
        _progress.detail = None
        _progress.current = 0
        _progress.total = None
        _progress.cancellation_requested = False
        _persist_callback = None
        _last_persisted_at = 0.0
        _last_persisted_phase = None


def reset_jellyfin_cancellation() -> None:
    _cancel_event.clear()
    with _lock:
        _progress.cancellation_requested = False


def request_jellyfin_cancellation(job_id: int | None = None) -> bool:
    with _lock:
        if job_id is not None and _progress.job_id not in {None, job_id}:
            return False
        _cancel_event.set()
        _progress.cancellation_requested = True
        return True


def jellyfin_cancellation_requested() -> bool:
    return _cancel_event.is_set()


def get_jellyfin_progress() -> dict[str, str | int | bool | None]:
    with _lock:
        return asdict(_progress)
