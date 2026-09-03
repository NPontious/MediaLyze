import json
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.models.entities import AppSetting
from backend.app.services.update_status import (
    DESKTOP_UPDATE_REMINDER_KEY,
    UPDATE_STATUS_KEY,
    check_for_updates,
    get_desktop_update_reminder,
    get_or_check_update_status,
    get_update_status,
    is_newer_stable_version,
    mark_desktop_update_reminder,
    parse_remote_release_notes,
)


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def test_stable_version_comparison_ignores_dev_and_prerelease_values() -> None:
    assert is_newer_stable_version("0.12.0", "0.11.0") is True
    assert is_newer_stable_version("v0.11.0", "0.11.0") is False
    assert is_newer_stable_version("0.12.0-beta.1", "0.11.0") is False
    assert is_newer_stable_version("0.12.0", "dev") is False


def test_parse_remote_release_notes_reads_released_versions_only() -> None:
    notes = parse_remote_release_notes(
        "\n".join(
            [
                "# Changelog",
                "## vUnreleased",
                "- hidden",
                "## v0.12.0",
                ">2026-05-15",
                "### New",
                "- add `download` button [#12](https://github.com/NPontious/MediaLyze/issues/12)",
                "## v0.11.0",
                "### Fixed",
                "- improve **history**",
            ]
        )
    )

    assert notes == [
        {
            "version": "0.12.0",
            "date": "2026-05-15",
            "sections": [{"title": "New", "items": ["add download button [#12](https://github.com/NPontious/MediaLyze/issues/12)"]}],
        },
        {
            "version": "0.11.0",
            "date": None,
            "sections": [{"title": "Fixed", "items": ["improve history"]}],
        },
    ]


def test_update_check_persists_latest_stable_release_and_remote_notes(monkeypatch) -> None:
    settings = Settings()
    settings.app_version = "0.11.0"
    session_factory = _session_factory()

    def fake_get_text(url: str, _timeout: float) -> str:
        if url.endswith("/latest"):
            return json.dumps({"tag_name": "v0.12.0", "draft": False, "prerelease": False})
        return "## v0.12.0\n\n### New\n\n- newer release"

    monkeypatch.setattr("backend.app.services.update_status._get_text", fake_get_text)

    with session_factory() as db:
        status = check_for_updates(db, settings)

    assert status is not None
    assert status.latest_version == "0.12.0"
    assert status.update_available is True
    assert status.release_notes[0].version == "0.12.0"


def test_release_body_is_primary_and_valid_assets_are_exposed_when_changelog_fails(monkeypatch) -> None:
    settings = Settings()
    settings.app_version = "0.11.0"
    session_factory = _session_factory()

    def fake_get_text(url: str, _timeout: float) -> str:
        if url.endswith("/latest"):
            return json.dumps(
                {
                    "tag_name": "v0.12.0",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-05-15T12:00:00Z",
                    "body": "### New\n\n- primary release body",
                    "assets": [
                        {
                            "state": "uploaded",
                            "name": "MediaLyze-arm64.dmg",
                            "size": 123,
                            "digest": f"sha256:{'a' * 64}",
                            "browser_download_url": (
                                "https://github.com/frederikemmer/MediaLyze/releases/"
                                "download/v0.12.0/MediaLyze-arm64.dmg"
                            ),
                        },
                        {
                            "state": "uploaded",
                            "name": "MediaLyze-x64.dmg",
                            "size": 456,
                            "digest": f"sha256:{'b' * 64}",
                            "browser_download_url": (
                                "https://github.com/frederikemmer/MediaLyze/releases/"
                                "download/v0.12.0/MediaLyze-x64.dmg"
                            ),
                        },
                    ],
                }
            )
        raise OSError("changelog unavailable")

    monkeypatch.setattr("backend.app.services.update_status._get_text", fake_get_text)

    with session_factory() as db:
        status = check_for_updates(db, settings)

    assert status is not None
    assert status.latest_release_url.endswith("/releases/tag/v0.12.0")
    assert status.release_notes[0].sections[0].items == ["primary release body"]
    assert status.release_notes[0].date == "2026-05-15"
    assert [asset.filename for asset in status.desktop_assets] == ["MediaLyze-arm64.dmg"]
    assert status.desktop_assets[0].sha256 == "a" * 64


def test_failed_update_check_keeps_last_successful_result(monkeypatch) -> None:
    settings = Settings()
    settings.app_version = "0.11.0"
    session_factory = _session_factory()

    with session_factory() as db:
        db.add(
            AppSetting(
                key=UPDATE_STATUS_KEY,
                value={
                    "latest_version": "0.12.0",
                    "checked_at": datetime(2026, 5, 15, tzinfo=UTC).isoformat(),
                    "release_notes": [],
                },
            )
        )
        db.commit()

        monkeypatch.setattr(
            "backend.app.services.update_status._get_text",
            lambda *_args: (_ for _ in ()).throw(OSError("offline")),
        )

        assert check_for_updates(db, settings) is None
        status = get_update_status(db, settings)

    assert status.latest_version == "0.12.0"
    assert status.update_available is True


def test_missing_asset_retry_stops_after_six_failed_fast_attempts(monkeypatch) -> None:
    settings = Settings()
    settings.app_version = "0.11.0"
    session_factory = _session_factory()
    now = datetime.now(UTC)
    calls = 0

    def fail_get_text(*_args) -> str:
        nonlocal calls
        calls += 1
        raise OSError("offline")

    monkeypatch.setattr("backend.app.services.update_status._get_text", fail_get_text)

    with session_factory() as db:
        db.add(
            AppSetting(
                key=UPDATE_STATUS_KEY,
                value={
                    "latest_version": "0.12.0",
                    "checked_at": now.isoformat(),
                    "release_notes": [],
                    "desktop_assets": [],
                    "asset_retry_count": 5,
                    "asset_retry_after": (now.replace(year=now.year - 1)).isoformat(),
                },
            )
        )
        db.commit()

        failed_status = get_or_check_update_status(db, settings)
        stored = db.get(AppSetting, UPDATE_STATUS_KEY)
        assert stored is not None
        assert stored.value["asset_retry_count"] == 6
        assert stored.value["asset_retry_after"] is None

        cached_status = get_or_check_update_status(db, settings)

    assert failed_status.automatic_reminder_eligible is False
    assert cached_status.update_available is True
    assert calls == 1


def test_get_or_check_update_status_checks_when_no_result_is_cached(monkeypatch) -> None:
    settings = Settings()
    settings.app_version = "0.11.0"
    session_factory = _session_factory()

    def fake_get_text(url: str, _timeout: float) -> str:
        if url.endswith("/latest"):
            return json.dumps({"tag_name": "v0.12.0", "draft": False, "prerelease": False})
        return "## v0.12.0\n\n### New\n\n- newer release"

    monkeypatch.setattr("backend.app.services.update_status._get_text", fake_get_text)

    with session_factory() as db:
        status = get_or_check_update_status(db, settings)

    assert status.latest_version == "0.12.0"
    assert status.update_available is True


def test_desktop_update_reminder_is_installation_wide_and_cleans_invalid_values() -> None:
    settings = Settings()
    settings.app_version = "0.11.0"
    session_factory = _session_factory()

    with session_factory() as db:
        db.add(
            AppSetting(
                key=UPDATE_STATUS_KEY,
                value={
                    "latest_version": "0.12.0",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "release_notes": [],
                },
            )
        )
        db.add(
            AppSetting(
                key=DESKTOP_UPDATE_REMINDER_KEY,
                value={"version": "invalid", "reminded_at": "2999-01-01T00:00:00Z"},
            )
        )
        db.commit()

        assert get_desktop_update_reminder(db).reminded_at is None
        marked = mark_desktop_update_reminder(db, settings, "0.12.0")
        loaded = get_desktop_update_reminder(db)

    assert marked.version == "0.12.0"
    assert marked.reminded_at is not None
    assert loaded == marked
