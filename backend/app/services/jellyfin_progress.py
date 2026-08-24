from __future__ import annotations

from dataclasses import asdict, dataclass, field
from threading import Event, Lock
from time import monotonic
from collections.abc import Callable


@dataclass(slots=True)
class JellyfinSyncProgressTrack:
    id: str
    label: str
    current: int = 0
    total: int | None = None
    status: str = "queued"


@dataclass(slots=True)
class JellyfinSyncProgress:
    job_id: int | None = None
    phase: str | None = None
    detail: str | None = None
    current: int = 0
    total: int | None = None
    cancellation_requested: bool = False
    tracks: dict[str, JellyfinSyncProgressTrack] = field(default_factory=dict)


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
        _progress.tracks.clear()
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
        if phase != _progress.phase:
            _progress.tracks.clear()
        _progress.phase = phase
        _progress.detail = detail
        _progress.current = current
        _progress.total = total
        now = monotonic()
        if _persist_callback is not None and (
            phase != _last_persisted_phase
            or now - _last_persisted_at >= 0.5
            or (total is not None and current >= total)
        ):
            callback = _persist_callback
            _last_persisted_at = now
            _last_persisted_phase = phase
    if callback is not None:
        callback(phase=phase, detail=detail, current=current, total=total)


def set_jellyfin_progress_tracks(tracks: list[tuple[str, str]]) -> None:
    with _lock:
        _progress.tracks = {
            track_id: JellyfinSyncProgressTrack(id=track_id, label=label)
            for track_id, label in tracks
        }


def update_jellyfin_progress_track(
    track_id: str,
    *,
    current: int,
    total: int | None,
) -> None:
    global _last_persisted_at
    callback = None
    callback_payload = None
    with _lock:
        track = _progress.tracks.get(track_id)
        if track is None:
            return
        track.current = max(0, int(current))
        track.total = max(0, int(total)) if total is not None else None
        track.status = "running"
        now = monotonic()
        if _persist_callback is not None and now - _last_persisted_at >= 0.5:
            callback = _persist_callback
            callback_payload = {
                "phase": _progress.phase,
                "detail": _progress.detail,
                "current": _progress.current,
                "total": _progress.total,
            }
            _last_persisted_at = now
    if callback is not None and callback_payload is not None:
        callback(**callback_payload)


def complete_jellyfin_progress_track(track_id: str) -> None:
    with _lock:
        track = _progress.tracks.get(track_id)
        if track is None:
            return
        if track.total is not None:
            track.current = track.total
        track.status = "completed"


def clear_jellyfin_progress() -> None:
    global _persist_callback, _last_persisted_at, _last_persisted_phase
    with _lock:
        _progress.job_id = None
        _progress.phase = None
        _progress.detail = None
        _progress.current = 0
        _progress.total = None
        _progress.cancellation_requested = False
        _progress.tracks.clear()
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


def get_jellyfin_progress() -> dict:
    with _lock:
        progress = asdict(_progress)
        progress["tracks"] = [
            asdict(track) for track in _progress.tracks.values()
        ]
        return progress
