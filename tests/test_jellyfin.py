from datetime import UTC, datetime
import os
from pathlib import Path
from threading import Lock
import tempfile
from types import SimpleNamespace

os.environ.setdefault("CONFIG_PATH", tempfile.mkdtemp(prefix="medialyze-config-"))
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="medialyze-media-"))

import httpx
import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.deps import get_app_settings, get_db_session
from backend.app.api.routes import router
from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.models.entities import (
    HistoryAddedDateSource,
    JellyfinConnection,
    JellyfinItem,
    JellyfinLibrary,
    JellyfinMediaMatch,
    JellyfinPathMapping,
    JellyfinSyncJob,
    JellyfinSyncTriggerSource,
    JellyfinUser,
    JellyfinUserItemData,
    JobStatus,
    Library,
    LibraryRoot,
    LibraryType,
    MediaFile,
    ScanMode,
    ScanStatus,
)
from backend.app.services.history_reconstruction import reconstruct_history_from_media_files
from backend.app.services.jellyfin_client import (
    JellyfinClient,
    JellyfinConfigurationError,
    JellyfinItemPage,
    JellyfinImage,
    JellyfinConnectionError,
    JellyfinResponseError,
)
from backend.app.services.jellyfin_images import JellyfinImageCache
from backend.app.services.jellyfin_matching import apply_path_mappings, recompute_jellyfin_matches
from backend.app.services.jellyfin_progress import (
    clear_jellyfin_progress,
    request_jellyfin_cancellation,
    reset_jellyfin_cancellation,
    update_jellyfin_progress,
)
from backend.app.services.jellyfin_sync import run_jellyfin_sync
from backend.app.services.media_service import list_library_files
from backend.app.services.runtime import ScanRuntimeManager


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as session:
        yield session


class _Runtime:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler()
        self.jellyfin_match_recompute_requests = 0
        self.jellyfin_sync_job: dict | None = None

    def refresh_jellyfin_schedule(self) -> None:
        return None

    def sync_library(self, _library_id: int) -> None:
        return None

    def request_jellyfin_match_recompute(self) -> bool:
        self.jellyfin_match_recompute_requests += 1
        return True

    def get_jellyfin_match_recompute_status(self) -> dict:
        return {
            "status": "queued" if self.jellyfin_match_recompute_requests else "idle",
            "active": bool(self.jellyfin_match_recompute_requests),
            "rerun_pending": False,
            "last_error": None,
        }

    def request_jellyfin_sync(self) -> dict:
        accepted = self.jellyfin_sync_job is None
        if accepted:
            self.jellyfin_sync_job = {
                "job_id": 1,
                "status": "queued",
                "trigger_source": "manual",
            }
        return {**self.jellyfin_sync_job, "accepted": accepted}

    def cancel_jellyfin_sync(self, job_id: int | None = None) -> dict:
        if self.jellyfin_sync_job is None or (
            job_id is not None and job_id != self.jellyfin_sync_job["job_id"]
        ):
            return {"job_id": None, "status": None, "cancellation_requested": False}
        request_jellyfin_cancellation()
        return {
            "job_id": self.jellyfin_sync_job["job_id"],
            "status": self.jellyfin_sync_job["status"],
            "cancellation_requested": True,
        }


def _client(db: Session, settings: Settings | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.scan_runtime = _Runtime()
    app.dependency_overrides[get_db_session] = lambda: db
    if settings is not None:
        app.dependency_overrides[get_app_settings] = lambda: settings
    return TestClient(app)


def _add_media(db: Session, root: Path, relative_path: str = "Movie.mkv") -> MediaFile:
    library = Library(
        name="Movies",
        path=str(root),
        type=LibraryType.movies,
        scan_mode=ScanMode.manual,
    )
    db.add(library)
    db.flush()
    library_root = LibraryRoot(
        library_id=library.id,
        path=str(root),
        display_name=root.name,
        path_key=str(root).casefold(),
    )
    db.add(library_root)
    db.flush()
    media = MediaFile(
        library_id=library.id,
        library_root_id=library_root.id,
        relative_path=relative_path,
        filename=Path(relative_path).name,
        extension=Path(relative_path).suffix,
        size_bytes=1234,
        mtime=datetime.now(UTC).timestamp(),
        scan_status=ScanStatus.ready,
        duration_seconds=120.0,
    )
    db.add(media)
    db.commit()
    return media


def test_library_file_rows_include_metadata_only_for_matched_jellyfin_items(db: Session, tmp_path: Path) -> None:
    matched_media = _add_media(db, tmp_path, "Matched.mkv")
    unmatched_media = MediaFile(
        library_id=matched_media.library_id,
        library_root_id=matched_media.library_root_id,
        relative_path="Unmatched.mkv",
        filename="Unmatched.mkv",
        extension="mkv",
        size_bytes=4321,
        mtime=datetime.now(UTC).timestamp(),
        scan_status=ScanStatus.ready,
    )
    item = JellyfinItem(
        jellyfin_item_id="jf-matched",
        item_type="Movie",
        title="Matched catalog title",
        production_year=2024,
        date_created=datetime(2025, 1, 2, tzinfo=UTC),
        series_name="Example series",
        season_name="Season 1",
    )
    db.add_all([unmatched_media, item])
    db.flush()
    db.add(JellyfinMediaMatch(
        media_file_id=matched_media.id,
        jellyfin_item_id=item.id,
        match_method="path",
        confidence=1.0,
        status="matched",
    ))
    db.add_all([
        JellyfinUser(jellyfin_user_id="user-a", name="A", enabled_for_sync=True),
        JellyfinUser(jellyfin_user_id="user-b", name="B", enabled_for_sync=True),
        JellyfinUser(jellyfin_user_id="user-disabled", name="Disabled", enabled_for_sync=False),
    ])
    db.flush()
    db.add_all([
        JellyfinUserItemData(jellyfin_item_id=item.id, jellyfin_user_id="user-a", play_count=2, played=True),
        JellyfinUserItemData(jellyfin_item_id=item.id, jellyfin_user_id="user-b", play_count=1, played=False),
        JellyfinUserItemData(jellyfin_item_id=item.id, jellyfin_user_id="user-disabled", play_count=99, played=True),
    ])
    db.commit()

    page = list_library_files(db, matched_media.library_id, limit=10)
    rows = {row.filename: row for row in page.items}

    assert rows["Matched.mkv"].jellyfin_title == "Matched catalog title"
    assert rows["Matched.mkv"].jellyfin_production_year == 2024
    assert rows["Matched.mkv"].jellyfin_play_count == 3
    assert rows["Matched.mkv"].jellyfin_played_user_count == 1
    assert rows["Unmatched.mkv"].jellyfin_title is None
    assert rows["Unmatched.mkv"].jellyfin_play_count is None


def test_client_rejects_malformed_url() -> None:
    with pytest.raises(JellyfinConfigurationError):
        JellyfinClient("jellyfin.local:8096", "secret")


def test_client_sends_api_key_and_timeout() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(url=str(request.url), headers=request.headers)
        return httpx.Response(200, json={"ServerName": "Test"})

    client = JellyfinClient(
        "http://jellyfin:8096",
        "secret",
        timeout_seconds=7,
        transport=httpx.MockTransport(handler),
    )
    client.get_system_info()
    assert captured["headers"]["X-Emby-Token"] == "secret"
    assert client._client.timeout.read == 7


def test_client_paginates_items() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        requests.append(params)
        start_index = int(params["StartIndex"])
        item_count = JellyfinClient.ITEM_PAGE_SIZE if start_index == 0 else 2
        return httpx.Response(
            200,
            json={
                "Items": [
                    {"Id": f"item-{start_index + offset}"}
                    for offset in range(item_count)
                ],
                "TotalRecordCount": JellyfinClient.ITEM_PAGE_SIZE + 2,
            },
        )

    items = JellyfinClient(
        "http://jellyfin:8096", "secret", transport=httpx.MockTransport(handler)
    ).get_items(user_id="user-1")

    assert len(items) == JellyfinClient.ITEM_PAGE_SIZE + 2
    assert [int(request["StartIndex"]) for request in requests] == [0, JellyfinClient.ITEM_PAGE_SIZE]
    assert all(int(request["Limit"]) == JellyfinClient.ITEM_PAGE_SIZE for request in requests)
    assert all(request["UserId"] == "user-1" for request in requests)


def test_client_reports_item_page_progress() -> None:
    progress: list[tuple[int, int | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start_index = int(request.url.params["StartIndex"])
        count = JellyfinClient.ITEM_PAGE_SIZE if start_index == 0 else 1
        return httpx.Response(
            200,
            json={
                "Items": [{"Id": str(start_index + offset)} for offset in range(count)],
                "TotalRecordCount": JellyfinClient.ITEM_PAGE_SIZE + 1,
            },
        )

    JellyfinClient(
        "http://jellyfin:8096", "secret", transport=httpx.MockTransport(handler)
    ).get_items(
        progress_callback=lambda current, total: progress.append((current, total))
    )

    assert progress == [
        (JellyfinClient.ITEM_PAGE_SIZE, JellyfinClient.ITEM_PAGE_SIZE + 1),
        (JellyfinClient.ITEM_PAGE_SIZE + 1, JellyfinClient.ITEM_PAGE_SIZE + 1),
    ]


def test_client_retries_transient_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("slow response")
        return httpx.Response(200, json={"Version": "10.11"})

    monkeypatch.setattr("backend.app.services.jellyfin_client.sleep", lambda _seconds: None)

    assert JellyfinClient(
        "http://jellyfin:8096", "secret", transport=httpx.MockTransport(handler)
    ).get_system_info()["Version"] == "10.11"
    assert attempts == 3


def test_client_rejects_cross_origin_redirect_without_forwarding_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/collect"})

    client = JellyfinClient(
        "https://jellyfin.example",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(JellyfinConnectionError, match="different origin"):
        client.get_system_info()

    assert len(requests) == 1
    assert requests[0].headers["X-Emby-Token"] == "secret"
    assert requests[0].url.host == "jellyfin.example"


def test_client_follows_only_same_origin_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/System/Info":
            return httpx.Response(307, headers={"Location": "/api/System/Info"})
        return httpx.Response(200, json={"Version": "10.11"})

    result = JellyfinClient(
        "https://jellyfin.example",
        "secret",
        transport=httpx.MockTransport(handler),
    ).get_system_info()

    assert result["Version"] == "10.11"
    assert [request.url.host for request in requests] == ["jellyfin.example", "jellyfin.example"]


@pytest.mark.parametrize(
    ("path", "payload", "method"),
    [
        ("/Users", {"Users": []}, "get_users"),
        ("/Library/VirtualFolders", {}, "get_virtual_folders"),
        ("/Items", {"Items": []}, "get_items"),
    ],
)
def test_client_rejects_malformed_success_payloads(path: str, payload: object, method: str) -> None:
    client = JellyfinClient(
        "https://jellyfin.example",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
            if request.url.path == path
            else httpx.Response(404)
        ),
    )
    with pytest.raises(JellyfinResponseError):
        getattr(client, method)()


def test_client_retries_transient_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"Version": "10.11"})

    monkeypatch.setattr("backend.app.services.jellyfin_client.sleep", lambda _seconds: None)
    result = JellyfinClient(
        "https://jellyfin.example",
        "secret",
        transport=httpx.MockTransport(handler),
    ).get_system_info()
    assert result["Version"] == "10.11"
    assert attempts == 3


def test_image_cache_is_server_scoped_and_byte_bounded() -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, base_url: str):
            self.base_url = base_url

        def get_image(self, *_args, **_kwargs):
            calls.append(self.base_url)
            return JellyfinImage(content=self.base_url.encode().ljust(16, b"x"), content_type="image/jpeg")

    cache = JellyfinImageCache(max_entries=10, max_bytes=20, max_item_bytes=20)
    first = cache.get(FakeClient("https://one.example"), "item", "Primary", "tag")
    second = cache.get(FakeClient("https://two.example"), "item", "Primary", "tag")

    assert first.content != second.content
    assert calls == ["https://one.example", "https://two.example"]
    assert len(cache._entries) == 1
    assert cache._size_bytes <= 20


def test_path_mapping_and_exact_match(db: Session, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    media = _add_media(db, media_root)
    mapping = JellyfinPathMapping(
        jellyfin_path_prefix="/jellyfin/media",
        medialyze_path_prefix=str(media_root),
        enabled=True,
    )
    item = JellyfinItem(
        jellyfin_item_id="jf-1",
        item_type="Movie",
        path="/jellyfin/media/Movie.mkv",
        title="Movie",
    )
    db.add_all([mapping, item])
    db.commit()

    mapped, applied = apply_path_mappings(item.path, [mapping])
    assert applied is True
    assert mapped.casefold().endswith("/media/movie.mkv")
    summary = recompute_jellyfin_matches(db)
    assert summary == {"matches_created": 1, "unmatched_items": 0}
    match = db.scalar(select(JellyfinMediaMatch))
    assert match is not None and match.media_file_id == media.id
    assert item.match_status == "matched"


def test_match_recompute_checks_each_mapping_root_once(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    _add_media(db, media_root)
    db.add(JellyfinPathMapping(
        jellyfin_path_prefix="/jellyfin/media",
        medialyze_path_prefix=str(media_root),
        enabled=True,
    ))
    db.add_all([
        JellyfinItem(jellyfin_item_id="jf-cache-1", item_type="Movie", path="/jellyfin/media/One.mkv", title="One"),
        JellyfinItem(jellyfin_item_id="jf-cache-2", item_type="Movie", path="/jellyfin/media/Two.mkv", title="Two"),
    ])
    db.commit()
    exists_calls = 0

    def tracked_exists(_path: Path) -> bool:
        nonlocal exists_calls
        exists_calls += 1
        return True

    monkeypatch.setattr("backend.app.services.jellyfin_matching.Path.exists", tracked_exists)

    recompute_jellyfin_matches(db)

    assert exists_calls == 1


def test_unmapped_and_inaccessible_reasons(db: Session, tmp_path: Path) -> None:
    _add_media(db, tmp_path / "existing")
    unmapped = JellyfinItem(
        jellyfin_item_id="unmapped",
        item_type="Movie",
        path="/remote/Movie.mkv",
        title="Unmapped",
    )
    inaccessible = JellyfinItem(
        jellyfin_item_id="inaccessible",
        item_type="Movie",
        path="/jellyfin/Movie.mkv",
        title="Inaccessible",
    )
    db.add_all([
        unmapped,
        inaccessible,
        JellyfinPathMapping(
            jellyfin_path_prefix="/jellyfin",
            medialyze_path_prefix=str(tmp_path / "missing"),
            enabled=True,
        ),
    ])
    db.commit()
    recompute_jellyfin_matches(db)
    assert unmapped.mismatch_reason == "path_unmapped"
    assert inaccessible.mismatch_reason == "path_not_accessible"


def test_connection_api_never_returns_secret(db: Session) -> None:
    client = _client(db)
    response = client.patch(
        "/api/jellyfin/connection",
        json={"base_url": "http://jellyfin:8096", "api_key": "top-secret"},
    )
    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True
    assert "api_key" not in response.json()
    assert "top-secret" not in response.text


def test_connection_accepts_zero_to_disable_scheduled_sync(db: Session) -> None:
    response = _client(db).patch(
        "/api/jellyfin/connection",
        json={
            "base_url": "http://jellyfin:8096",
            "api_key": "secret",
            "enabled": True,
            "sync_interval_minutes": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["sync_interval_minutes"] == 0
    assert response.json()["next_scheduled_sync_at"] is None


def test_zero_interval_removes_scheduled_sync_job(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = SimpleNamespace(
        enabled=True,
        base_url="http://jellyfin:8096",
        api_key="secret",
        sync_interval_minutes=15,
    )

    class FakeSession:
        def get(self, _model, _record_id):
            return connection

        def close(self):
            return None

    monkeypatch.setattr("backend.app.services.runtime.SessionLocal", FakeSession)
    runtime = object.__new__(ScanRuntimeManager)
    runtime.scheduler = BackgroundScheduler()

    runtime.refresh_jellyfin_schedule()
    assert runtime.scheduler.get_job("jellyfin-sync") is not None

    connection.sync_interval_minutes = 0
    runtime.refresh_jellyfin_schedule()
    assert runtime.scheduler.get_job("jellyfin-sync") is None


def test_runtime_deduplicates_manual_and_scheduled_jellyfin_syncs(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.add(JellyfinConnection(
        id=1,
        base_url="http://jellyfin:8096",
        api_key="secret",
        enabled=True,
    ))
    db.commit()
    factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    class FakeExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def submit(self, function, *args):
            self.calls.append((function, args))
            return SimpleNamespace()

    executor = FakeExecutor()
    runtime = object.__new__(ScanRuntimeManager)
    runtime.started = True
    runtime.lock = Lock()
    runtime.maintenance_executor = executor
    monkeypatch.setattr("backend.app.services.runtime.SessionLocal", factory)

    manual = runtime.request_jellyfin_sync(JellyfinSyncTriggerSource.manual)
    scheduled = runtime.request_jellyfin_sync(JellyfinSyncTriggerSource.scheduled)

    assert manual["accepted"] is True
    assert scheduled == {**manual, "accepted": False}
    assert len(executor.calls) == 1
    assert executor.calls[0][1] == (manual["job_id"],)
    jobs = list(db.scalars(select(JellyfinSyncJob)))
    assert [(job.id, job.status.value, job.active_lock) for job in jobs] == [
        (manual["job_id"], "queued", 1)
    ]
    monkeypatch.setattr(
        "backend.app.services.runtime.run_jellyfin_sync",
        lambda _db, *, job_id: {
            "status": "success",
            "items_synced": 12,
            "libraries_synced": 2,
            "users_synced": 1,
            "matches_created": 8,
            "unmatched_items": 4,
            "job_id": job_id,
        },
    )
    function, args = executor.calls[0]
    function(*args)
    db.expire_all()
    completed = db.get(JellyfinSyncJob, manual["job_id"])
    assert completed is not None
    assert completed.status.value == "completed"
    assert completed.active_lock is None
    assert completed.sync_summary["items_synced"] == 12

    queued = runtime.request_jellyfin_sync(JellyfinSyncTriggerSource.scheduled)
    assert queued["accepted"] is True
    cancel_result = runtime.cancel_jellyfin_sync(queued["job_id"])
    assert cancel_result == {
        "job_id": queued["job_id"],
        "status": "canceled",
        "cancellation_requested": True,
    }
    queued_function, queued_args = executor.calls[1]
    queued_function(*queued_args)
    db.expire_all()
    canceled = db.get(JellyfinSyncJob, queued["job_id"])
    assert canceled is not None
    assert canceled.status.value == "canceled"
    assert canceled.active_lock is None


def test_sync_status_exposes_live_progress(db: Session) -> None:
    client = _client(db)
    update_jellyfin_progress("items", detail="Alice", current=500, total=1200)
    try:
        response = client.get("/api/jellyfin/sync/status")
    finally:
        clear_jellyfin_progress()

    assert response.status_code == 200
    payload = response.json()
    assert {
        key: payload[key]
        for key in ("sync_phase", "sync_phase_detail", "sync_current", "sync_total")
    } == {
        "sync_phase": "items",
        "sync_phase_detail": "Alice",
        "sync_current": 500,
        "sync_total": 1200,
    }


def test_sync_status_exposes_persisted_active_job(db: Session) -> None:
    heartbeat = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
    job = JellyfinSyncJob(
        status=JobStatus.running,
        trigger_source=JellyfinSyncTriggerSource.scheduled,
        active_lock=1,
        heartbeat_at=heartbeat,
        progress_phase="saving",
        progress_detail="Alice",
        progress_current=750,
        progress_total=1200,
    )
    db.add(job)
    db.commit()

    payload = _client(db).get("/api/jellyfin/sync/status").json()

    assert payload["sync_job_id"] == job.id
    assert payload["sync_job_status"] == "running"
    assert payload["sync_trigger_source"] == "scheduled"
    assert payload["sync_job_active"] is True
    assert payload["sync_heartbeat_at"] == "2026-07-21T08:00:00Z"
    assert payload["sync_phase"] == "saving"
    assert payload["sync_phase_detail"] == "Alice"
    assert payload["sync_current"] == 750
    assert payload["sync_total"] == 1200


def test_manual_sync_returns_accepted_job_without_running_inline(db: Session) -> None:
    client = _client(db)

    first = client.post("/api/jellyfin/sync")
    second = client.post("/api/jellyfin/sync")

    assert first.status_code == 202
    assert first.json() == {
        "job_id": 1,
        "status": "queued",
        "trigger_source": "manual",
        "accepted": True,
    }
    assert second.status_code == 202
    assert second.json() == {**first.json(), "accepted": False}


def test_mapping_change_returns_before_background_match_recompute(db: Session) -> None:
    library = JellyfinLibrary(
        name="Movies",
        locations=["/remote/movies"],
        mapped_status="path_unmapped",
    )
    db.add(library)
    db.commit()
    client = _client(db)

    response = client.post(
        "/api/jellyfin/path-mappings",
        json={
            "jellyfin_path_prefix": "/remote",
            "medialyze_path_prefix": "/media",
            "enabled": True,
        },
    )

    assert response.status_code == 201
    db.refresh(library)
    assert library.mapped_status == "updating"
    assert client.app.state.scan_runtime.jellyfin_match_recompute_requests == 1
    assert client.get("/api/jellyfin/matches/recompute/status").json() == {
        "status": "queued",
        "active": True,
        "rerun_pending": False,
        "last_error": None,
    }


def test_sync_cancel_endpoint_marks_running_sync_for_cancellation(db: Session) -> None:
    db.add(JellyfinConnection(id=1, last_status="running"))
    db.commit()
    client = _client(db)
    client.app.state.scan_runtime.jellyfin_sync_job = {
        "job_id": 1,
        "status": "queued",
        "trigger_source": "manual",
    }
    try:
        response = client.post("/api/jellyfin/sync/cancel")
        status_response = client.get("/api/jellyfin/sync/status")
    finally:
        reset_jellyfin_cancellation()
        clear_jellyfin_progress()

    assert response.status_code == 200
    assert response.json() == {
        "job_id": 1,
        "status": "queued",
        "cancellation_requested": True,
    }
    assert status_response.json()["cancellation_requested"] is True


def test_canceled_sync_rolls_back_partial_catalog_changes(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.add_all([
        JellyfinConnection(
            id=1,
            base_url="http://jellyfin:8096",
            api_key="secret",
            enabled=True,
        ),
        JellyfinItem(
            jellyfin_item_id="cached-item",
            item_type="Movie",
            title="Cached movie",
        ),
    ])
    db.commit()

    class CancelingClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_system_info(self):
            return {"ServerName": "Test", "Version": "10.11"}

        def get_users(self):
            return []

        def get_virtual_folders(self):
            return [{"Name": "Movies", "CollectionType": "movies", "Locations": ["/media"]}]

        def get_items(self, *, user_id=None, progress_callback=None):
            request_jellyfin_cancellation()
            if progress_callback is not None:
                progress_callback(1, 1)
            return [{"Id": "partial-item", "Type": "Movie", "Name": "Partial"}]

    monkeypatch.setattr("backend.app.services.jellyfin_sync.JellyfinClient", CancelingClient)

    result = run_jellyfin_sync(db)

    assert result["status"] == "canceled"
    assert db.get(JellyfinConnection, 1).last_status == "canceled"
    assert db.scalar(select(JellyfinItem).where(JellyfinItem.jellyfin_item_id == "cached-item")) is not None
    assert db.scalar(select(JellyfinItem).where(JellyfinItem.jellyfin_item_id == "partial-item")) is None


def test_sync_persists_separate_user_data(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    _add_media(db, media_root)
    db.add_all([
        JellyfinConnection(
            id=1,
            base_url="http://jellyfin:8096",
            api_key="secret",
            enabled=True,
        ),
        JellyfinPathMapping(
            jellyfin_path_prefix="/remote",
            medialyze_path_prefix=str(media_root),
            enabled=True,
        ),
    ])
    db.commit()

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_system_info(self):
            return {"ServerName": "Test", "Version": "10.10"}

        def get_users(self):
            return [{"Id": "u1", "Name": "Alice"}, {"Id": "u2", "Name": "Bob"}]

        def get_virtual_folders(self):
            return [{"Name": "Movies", "CollectionType": "movies", "Locations": ["/remote"]}]

        def get_items(self, *, user_id=None, progress_callback=None):
            count = 1 if user_id == "u1" else 3
            items = [{
                "Id": "item-1",
                "Name": "Movie",
                "Type": "Movie",
                "Path": "/remote/Movie.mkv",
                "DateCreated": "2024-01-01T12:00:00Z",
                "UserData": {"PlayCount": count, "Played": count > 1},
            }]
            if progress_callback:
                progress_callback(len(items), len(items))
            return items

    monkeypatch.setattr("backend.app.services.jellyfin_sync.JellyfinClient", FakeClient)
    # Discover users first, then select both for the next synchronization.
    run_jellyfin_sync(db)
    for user in db.query(JellyfinUser).all():
        user.enabled_for_sync = True
    db.commit()
    run_jellyfin_sync(db)

    rows = list(db.scalars(select(JellyfinUserItemData).order_by(JellyfinUserItemData.jellyfin_user_id)))
    assert [(row.jellyfin_user_id, row.play_count) for row in rows] == [("u1", 1), ("u2", 3)]


def test_history_uses_fallback_when_jellyfin_date_is_missing(db: Session, tmp_path: Path) -> None:
    media = _add_media(db, tmp_path)
    library = db.get(Library, media.library_id)
    library.history_added_date_source = HistoryAddedDateSource.jellyfin
    db.commit()
    result = reconstruct_history_from_media_files(db)
    assert result.jellyfin_added_dates_used == 0
    assert result.jellyfin_added_date_fallbacks == 1


def test_sync_normalizes_size_from_primary_media_source(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    db.add(JellyfinConnection(base_url="https://jellyfin.example", api_key="secret", enabled=True))
    db.commit()
    monkeypatch.setattr(JellyfinClient, "get_system_info", lambda _self: {"ServerName": "Test"})
    monkeypatch.setattr(JellyfinClient, "get_users", lambda _self: [])
    monkeypatch.setattr(
        JellyfinClient,
        "get_virtual_folders",
        lambda _self: [{"Name": "Movies", "CollectionType": "movies", "Locations": ["/media"]}],
    )
    monkeypatch.setattr(
        JellyfinClient,
        "iter_item_pages",
        lambda _self, **_kwargs: iter([JellyfinItemPage(items=[{
            "Id": "movie-with-source-size",
            "Type": "Movie",
            "Name": "Movie",
            "Path": "/media/Movie.mkv",
            "RunTimeTicks": 600_000_000,
            "MediaSources": [{"Size": 4_096}],
        }], start_index=0, total_record_count=1)]),
    )

    run_jellyfin_sync(db)

    item = db.scalar(select(JellyfinItem).where(JellyfinItem.jellyfin_item_id == "movie-with-source-size"))
    assert item is not None
    assert item.raw_limited_payload["Size"] == 4_096


def test_jellyfin_only_catalog_endpoints_expose_cached_analysis(db: Session, tmp_path: Path) -> None:
    media = _add_media(db, tmp_path / "linked")
    remote = JellyfinLibrary(
        name="Remote Anime",
        collection_type="tvshows",
        locations=["/remote/anime"],
        mapped_locations=[],
        mapped_status="path_unmapped",
    )
    linked = JellyfinLibrary(
        name="Movies",
        collection_type="movies",
        locations=["/remote/movies"],
        mapped_locations=[str(tmp_path / "linked")],
        mapped_status="linked",
        linked_library_id=media.library_id,
    )
    db.add_all([remote, linked])
    db.flush()
    known = JellyfinItem(
        jellyfin_item_id="episode-1",
        library_name="Remote Anime",
        item_type="Episode",
        path="/remote/anime/Show/S01E01.mkv",
        title="Pilot",
        series_name="Example Show",
        season_name="Season 1",
        production_year=2024,
        date_created=datetime(2024, 1, 15, tzinfo=UTC),
        image_tags={"Primary": "tag"},
        raw_limited_payload={"Size": 2_000, "RunTimeTicks": 600_000_000},
    )
    unknown = JellyfinItem(
        jellyfin_item_id="episode-2",
        library_name="Remote Anime",
        item_type="Episode",
        path="/remote/anime/Show/S01E02.mkv",
        title="Finale",
        series_name="Example Show",
        production_year=2023,
        date_created=datetime(2024, 2, 20, tzinfo=UTC),
        raw_limited_payload={},
    )
    linked_item = JellyfinItem(
        jellyfin_item_id="movie-1",
        library_name="Movies",
        item_type="Movie",
        title="Linked movie",
        raw_limited_payload={"Size": 99_000, "RunTimeTicks": 900_000_000},
    )
    alice = JellyfinUser(jellyfin_user_id="alice", name="Alice", enabled_for_sync=True)
    bob = JellyfinUser(jellyfin_user_id="bob", name="Bob", enabled_for_sync=True)
    db.add_all([known, unknown, linked_item, alice, bob])
    db.flush()
    db.add_all([
        JellyfinUserItemData(
            jellyfin_item_id=known.id,
            jellyfin_user_id="alice",
            play_count=2,
            played=True,
        ),
        JellyfinUserItemData(
            jellyfin_item_id=known.id,
            jellyfin_user_id="bob",
            play_count=1,
            played=False,
            is_favorite=True,
        ),
    ])
    db.commit()
    client = _client(db)

    libraries = client.get("/api/jellyfin/libraries").json()
    remote_payload = next(item for item in libraries if item["name"] == "Remote Anime")
    assert remote_payload["data_scope"] == "jellyfin_only"
    assert remote_payload["item_count"] == 2

    summary = client.get("/api/jellyfin/catalog/summary").json()
    assert summary["library_count"] == 1
    assert summary["item_count"] == 2
    assert summary["known_size_bytes"] == 2_000
    assert summary["size_known_count"] == 1
    assert summary["known_duration_seconds"] == 60
    assert summary["duration_known_count"] == 1

    overview = client.get(f"/api/jellyfin/libraries/{remote.id}/overview").json()
    assert overview["item_count"] == 2
    assert overview["playback_distribution"] == [
        {"label": "played", "value": 1},
        {"label": "unplayed", "value": 1},
    ]
    assert {item["label"]: item["value"] for item in overview["production_year_distribution"]} == {
        "2024": 1,
        "2023": 1,
    }

    bob_overview = client.get(
        f"/api/jellyfin/libraries/{remote.id}/overview?user_id=bob"
    ).json()
    assert bob_overview["playback_distribution"][0]["value"] == 0

    page = client.get(
        f"/api/jellyfin/libraries/{remote.id}/items",
        params={"search": "show", "sort_key": "year", "sort_direction": "desc"},
    ).json()
    assert [item["title"] for item in page["items"]] == ["Pilot", "Finale"]
    assert page["items"][0]["duration_seconds"] == 60
    assert page["items"][0]["play_count"] == 3
    assert page["items"][0]["played_user_count"] == 1
    assert page["items"][0]["favorite_user_count"] == 1

    played_page = client.get(
        f"/api/jellyfin/libraries/{remote.id}/items",
        params={"played": "true", "user_id": "alice"},
    ).json()
    assert [item["title"] for item in played_page["items"]] == ["Pilot"]

    detail = client.get(f"/api/jellyfin/items/{known.id}").json()
    assert detail["library_id"] == remote.id
    assert detail["size_bytes"] == 2_000
    assert detail["duration_seconds"] == 60
    assert [row["user_name"] for row in detail["user_data"]] == ["Alice", "Bob"]


def test_jellyfin_catalog_returns_not_found_and_rejects_unknown_user(db: Session) -> None:
    client = _client(db)
    assert client.get("/api/jellyfin/libraries/999/overview").status_code == 404
    assert client.get("/api/jellyfin/items/999").status_code == 404
    library = JellyfinLibrary(
        name="Remote",
        collection_type="mixed",
        locations=["/remote"],
        mapped_locations=[],
        mapped_status="path_unmapped",
    )
    db.add(library)
    db.commit()
    assert client.get(
        f"/api/jellyfin/libraries/{library.id}/overview?user_id=missing"
    ).status_code == 400


def test_jellyfin_library_link_can_be_changed_and_unlinked(db: Session, tmp_path: Path) -> None:
    first_media = _add_media(db, tmp_path / "first")
    second = Library(name="Series", path=str(tmp_path / "series"), type=LibraryType.series)
    first_remote = JellyfinLibrary(name="Movies remote", mapped_status="accessible")
    second_remote = JellyfinLibrary(name="Series remote", mapped_status="accessible")
    db.add_all([second, first_remote, second_remote])
    db.commit()
    client = _client(db)

    linked = client.patch(
        f"/api/jellyfin/libraries/{first_remote.id}/link",
        json={"linked_library_id": first_media.library_id},
    )
    assert linked.status_code == 200
    assert linked.json()["linked_library_id"] == first_media.library_id
    assert linked.json()["link_method"] == "manual"

    reassigned = client.patch(
        f"/api/jellyfin/libraries/{second_remote.id}/link",
        json={"linked_library_id": first_media.library_id},
    )
    assert reassigned.status_code == 200
    db.refresh(first_remote)
    assert first_remote.linked_library_id is None
    assert first_remote.link_method == "manual"

    unlinked = client.patch(
        f"/api/jellyfin/libraries/{second_remote.id}/link",
        json={"linked_library_id": None},
    )
    assert unlinked.status_code == 200
    assert unlinked.json()["linked_library_id"] is None
    assert client.patch(
        f"/api/jellyfin/libraries/{second_remote.id}/link",
        json={"linked_library_id": second.id + 1000},
    ).status_code == 404


def test_manual_jellyfin_library_link_survives_sync(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = _add_media(db, tmp_path / "manual")
    db.add_all([
        JellyfinConnection(base_url="https://jellyfin.example", api_key="secret", enabled=True),
        JellyfinLibrary(
            name="Movies",
            locations=["/remote/movies"],
            mapped_status="linked",
            linked_library_id=media.library_id,
            link_method="manual",
        ),
    ])
    db.commit()
    monkeypatch.setattr(JellyfinClient, "get_system_info", lambda _self: {"ServerName": "Test"})
    monkeypatch.setattr(JellyfinClient, "get_users", lambda _self: [])
    monkeypatch.setattr(
        JellyfinClient,
        "get_virtual_folders",
        lambda _self: [{"Name": "Movies", "CollectionType": "movies", "Locations": ["/different/path"]}],
    )
    monkeypatch.setattr(
        JellyfinClient,
        "iter_item_pages",
        lambda _self, **_kwargs: iter([JellyfinItemPage(items=[], start_index=0, total_record_count=0)]),
    )

    run_jellyfin_sync(db)

    remote = db.scalar(select(JellyfinLibrary).where(JellyfinLibrary.name == "Movies"))
    assert remote is not None
    assert remote.linked_library_id == media.library_id
    assert remote.link_method == "manual"
    assert remote.mapped_status == "linked"


def test_jellyfin_library_rename_preserves_identity_and_manual_link(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = _add_media(db, tmp_path / "renamed")
    folder_name = "Movies"

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_system_info(self):
            return {"ServerName": "Test", "Version": "10.11"}

        def get_users(self):
            return []

        def get_virtual_folders(self):
            return [{
                "ItemId": "library-remote-id",
                "Name": folder_name,
                "CollectionType": "movies",
                "Locations": ["/remote/movies"],
            }]

        def get_items(self, **_kwargs):
            return [{
                "Id": "movie-1",
                "Name": "Movie",
                "Type": "Movie",
                "Path": "/remote/movies/Movie.mkv",
            }]

    db.add(JellyfinConnection(base_url="https://jellyfin.example", api_key="secret", enabled=True))
    db.commit()
    monkeypatch.setattr("backend.app.services.jellyfin_sync.JellyfinClient", FakeClient)

    run_jellyfin_sync(db)
    library = db.scalar(
        select(JellyfinLibrary).where(JellyfinLibrary.remote_item_id == "library-remote-id")
    )
    assert library is not None
    original_id = library.id
    library.linked_library_id = media.library_id
    library.link_method = "manual"
    db.commit()

    folder_name = "Films"
    run_jellyfin_sync(db)

    renamed = db.scalar(
        select(JellyfinLibrary).where(JellyfinLibrary.remote_item_id == "library-remote-id")
    )
    item = db.scalar(select(JellyfinItem).where(JellyfinItem.jellyfin_item_id == "movie-1"))
    assert renamed is not None
    assert renamed.id == original_id
    assert renamed.name == "Films"
    assert renamed.linked_library_id == media.library_id
    assert item is not None and item.library_id == renamed.id and item.library_name == "Films"


def test_disabling_jellyfin_user_removes_cached_playback_data(db: Session) -> None:
    item = JellyfinItem(jellyfin_item_id="item", item_type="Movie", title="Movie")
    user = JellyfinUser(jellyfin_user_id="user", name="User", enabled_for_sync=True)
    db.add_all([item, user])
    db.flush()
    db.add(JellyfinUserItemData(
        jellyfin_item_id=item.id,
        jellyfin_user_id=user.jellyfin_user_id,
        play_count=4,
        played=True,
    ))
    db.commit()

    response = _client(db).patch("/api/jellyfin/users", json={"enabled_user_ids": []})

    assert response.status_code == 200
    assert db.scalar(select(JellyfinUserItemData)) is None


def test_completed_user_sync_removes_items_missing_for_that_user(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = JellyfinItem(jellyfin_item_id="first", item_type="Movie", title="First")
    second = JellyfinItem(jellyfin_item_id="second", item_type="Movie", title="Second")
    user = JellyfinUser(jellyfin_user_id="user", name="User", enabled_for_sync=True)
    db.add_all([
        JellyfinConnection(base_url="https://jellyfin.example", api_key="secret", enabled=True),
        first,
        second,
        user,
    ])
    db.flush()
    db.add_all([
        JellyfinUserItemData(jellyfin_item_id=first.id, jellyfin_user_id="user", play_count=1),
        JellyfinUserItemData(jellyfin_item_id=second.id, jellyfin_user_id="user", play_count=2),
    ])
    db.commit()

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_system_info(self):
            return {"ServerName": "Test", "Version": "10.11"}

        def get_users(self):
            return [{"Id": "user", "Name": "User"}]

        def get_virtual_folders(self):
            return []

        def get_items(self, *, user_id=None, progress_callback=None):
            items = [
                {"Id": "first", "Type": "Movie", "Name": "First"},
                {"Id": "second", "Type": "Movie", "Name": "Second"},
            ]
            if user_id:
                items = [{**items[0], "UserData": {"PlayCount": 3}}]
            if progress_callback:
                progress_callback(len(items), len(items))
            return items

    monkeypatch.setattr("backend.app.services.jellyfin_sync.JellyfinClient", FakeClient)
    run_jellyfin_sync(db)

    rows = list(db.scalars(select(JellyfinUserItemData)))
    assert [(row.jellyfin_item_id, row.play_count) for row in rows] == [(first.id, 3)]


def test_manual_match_reassignment_updates_displaced_item_status(db: Session, tmp_path: Path) -> None:
    media = _add_media(db, tmp_path / "manual-reassign")
    first = JellyfinItem(jellyfin_item_id="first", item_type="Movie", title="First")
    second = JellyfinItem(jellyfin_item_id="second", item_type="Movie", title="Second")
    db.add_all([first, second])
    db.commit()
    client = _client(db)

    assert client.post(
        "/api/jellyfin/matches",
        json={"jellyfin_item_id": first.id, "media_file_id": media.id},
    ).status_code == 201
    assert client.post(
        "/api/jellyfin/matches",
        json={"jellyfin_item_id": second.id, "media_file_id": media.id},
    ).status_code == 201

    db.refresh(first)
    db.refresh(second)
    assert first.match_status == "unmatched"
    assert first.mismatch_reason == "manual_match_reassigned"
    assert second.match_status == "matched"


def test_path_matching_marks_duplicate_remote_path_as_conflict(db: Session, tmp_path: Path) -> None:
    media_root = tmp_path / "duplicate-path"
    media_root.mkdir()
    _add_media(db, media_root)
    db.add(JellyfinPathMapping(
        jellyfin_path_prefix="/remote",
        medialyze_path_prefix=str(media_root),
        enabled=True,
    ))
    first = JellyfinItem(
        jellyfin_item_id="first", item_type="Movie", title="First", path="/remote/Movie.mkv"
    )
    second = JellyfinItem(
        jellyfin_item_id="second", item_type="Movie", title="Second", path="/remote/Movie.mkv"
    )
    db.add_all([first, second])
    db.commit()

    recompute_jellyfin_matches(db)

    db.refresh(first)
    db.refresh(second)
    assert {first.match_status, second.match_status} == {"matched", "ambiguous"}
    conflicted = first if first.match_status == "ambiguous" else second
    assert conflicted.mismatch_reason == "media_file_already_matched"
    assert len(list(db.scalars(select(JellyfinMediaMatch)))) == 1


def test_disconnect_removes_jellyfin_connection_and_cached_data(db: Session) -> None:
    db.add_all([
        JellyfinConnection(base_url="https://jellyfin.example", api_key="secret", enabled=True),
        JellyfinUser(jellyfin_user_id="user", name="User", enabled_for_sync=True),
        JellyfinLibrary(remote_item_id="library", name="Movies"),
        JellyfinItem(jellyfin_item_id="item", item_type="Movie", title="Movie"),
        JellyfinPathMapping(jellyfin_path_prefix="/remote", medialyze_path_prefix="/media"),
    ])
    db.commit()

    response = _client(db).delete("/api/jellyfin/connection")

    assert response.status_code == 204
    connection = db.get(JellyfinConnection, 1)
    assert connection is not None
    assert connection.enabled is False and connection.api_key == "" and connection.base_url == ""
    assert db.scalar(select(JellyfinItem)) is None
    assert db.scalar(select(JellyfinLibrary)) is None
    assert db.scalar(select(JellyfinUser)) is None
    assert db.scalar(select(JellyfinPathMapping)) is None
