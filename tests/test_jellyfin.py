from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

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
    JellyfinUser,
    JellyfinUserItemData,
    Library,
    LibraryRoot,
    LibraryType,
    MediaFile,
    ScanMode,
    ScanStatus,
)
from backend.app.services.history_reconstruction import reconstruct_history_from_media_files
from backend.app.services.jellyfin_client import JellyfinClient, JellyfinConfigurationError
from backend.app.services.jellyfin_matching import apply_path_mappings, recompute_jellyfin_matches
from backend.app.services.jellyfin_progress import clear_jellyfin_progress, update_jellyfin_progress
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

    def refresh_jellyfin_schedule(self) -> None:
        return None

    def sync_library(self, _library_id: int) -> None:
        return None


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


def test_client_sends_api_key_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(200, json={"ServerName": "Test"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    JellyfinClient("http://jellyfin:8096", "secret", timeout_seconds=7).get_system_info()
    assert captured["headers"]["X-Emby-Token"] == "secret"
    assert captured["timeout"] == 7


def test_client_paginates_items(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []

    def fake_get(url, **kwargs):
        params = kwargs["params"]
        requests.append(params)
        start_index = params["StartIndex"]
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
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    items = JellyfinClient("http://jellyfin:8096", "secret").get_items(user_id="user-1")

    assert len(items) == JellyfinClient.ITEM_PAGE_SIZE + 2
    assert [request["StartIndex"] for request in requests] == [0, JellyfinClient.ITEM_PAGE_SIZE]
    assert all(request["Limit"] == JellyfinClient.ITEM_PAGE_SIZE for request in requests)
    assert all(request["UserId"] == "user-1" for request in requests)


def test_client_reports_item_page_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    progress: list[tuple[int, int | None]] = []

    def fake_get(url, **kwargs):
        start_index = kwargs["params"]["StartIndex"]
        count = JellyfinClient.ITEM_PAGE_SIZE if start_index == 0 else 1
        return httpx.Response(
            200,
            json={
                "Items": [{"Id": str(start_index + offset)} for offset in range(count)],
                "TotalRecordCount": JellyfinClient.ITEM_PAGE_SIZE + 1,
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    JellyfinClient("http://jellyfin:8096", "secret").get_items(
        progress_callback=lambda current, total: progress.append((current, total))
    )

    assert progress == [
        (JellyfinClient.ITEM_PAGE_SIZE, JellyfinClient.ITEM_PAGE_SIZE + 1),
        (JellyfinClient.ITEM_PAGE_SIZE + 1, JellyfinClient.ITEM_PAGE_SIZE + 1),
    ]


def test_client_retries_transient_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def fake_get(url, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("slow response")
        return httpx.Response(200, json={"Version": "10.11"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("backend.app.services.jellyfin_client.sleep", lambda _seconds: None)

    assert JellyfinClient("http://jellyfin:8096", "secret").get_system_info()["Version"] == "10.11"
    assert attempts == 3


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
        "get_items",
        lambda _self, **_kwargs: [{
            "Id": "movie-with-source-size",
            "Type": "Movie",
            "Name": "Movie",
            "Path": "/media/Movie.mkv",
            "RunTimeTicks": 600_000_000,
            "MediaSources": [{"Size": 4_096}],
        }],
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
    monkeypatch.setattr(JellyfinClient, "get_items", lambda _self, **_kwargs: [])

    run_jellyfin_sync(db)

    remote = db.scalar(select(JellyfinLibrary).where(JellyfinLibrary.name == "Movies"))
    assert remote is not None
    assert remote.linked_library_id == media.library_id
    assert remote.link_method == "manual"
    assert remote.mapped_status == "linked"
