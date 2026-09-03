from __future__ import annotations

import json
import re
import ssl
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import certifi
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.models.entities import AppSetting
from backend.app.schemas.update_status import DesktopUpdateReminderRead, UpdateStatusRead
from backend.app.utils.time import utc_now

UPDATE_STATUS_KEY = "update_status"
DESKTOP_UPDATE_REMINDER_KEY = "desktop_update_reminder"
UPDATE_STATUS_MAX_AGE = timedelta(hours=12)
MISSING_ASSET_RETRY_AGE = timedelta(minutes=15)
MISSING_ASSET_RETRY_LIMIT = 6
LATEST_RELEASE_URL = "https://api.github.com/repos/NPontious/MediaLyze/releases/latest"
REMOTE_CHANGELOG_URL = "https://raw.githubusercontent.com/NPontious/MediaLyze/main/CHANGELOG.md"
RELEASE_PAGE_BASE = "https://github.com/NPontious/MediaLyze/releases/tag"
UPDATE_CHECK_TIMEOUT_SECONDS = 2.0
SEMVER_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
CHANGELOG_HEADING_PATTERN = re.compile(r"^##\s+v([0-9][^\s]*)\s*$", re.MULTILINE)
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
DESKTOP_ASSET_TARGETS = {
    "MediaLyze-arm64.dmg": ("darwin", "arm64"),
    "MediaLyze.Setup.exe": ("win32", "x64"),
    "MediaLyze.AppImage": ("linux", "x64"),
}


def normalize_stable_version(value: str | None) -> str | None:
    candidate = (value or "").strip()
    match = SEMVER_PATTERN.fullmatch(candidate)
    if match is None:
        return None
    return ".".join(match.groups())


def semver_key(value: str) -> tuple[int, int, int]:
    normalized = normalize_stable_version(value)
    if normalized is None:
        raise ValueError(f"Unsupported stable version: {value}")
    major, minor, patch = normalized.split(".")
    return int(major), int(minor), int(patch)


def is_newer_stable_version(candidate: str | None, current: str | None) -> bool:
    normalized_candidate = normalize_stable_version(candidate)
    normalized_current = normalize_stable_version(current)
    if normalized_candidate is None or normalized_current is None:
        return False
    return semver_key(normalized_candidate) > semver_key(normalized_current)


def _clean_markdown_text(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _clean_release_note_item_text(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_release_notes_block(version: str, block: str) -> dict | None:
    payload = {"version": version, "date": None, "sections": []}
    current_section: dict | None = None
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            payload["date"] = _clean_markdown_text(line[1:])
            continue
        section_match = re.match(r"^###\s+(.+)$", line)
        if section_match:
            current_section = {"title": _clean_markdown_text(section_match.group(1)), "items": []}
            payload["sections"].append(current_section)
            continue
        item_match = re.match(r"^-\s+(.+)$", line)
        if item_match:
            if current_section is None:
                current_section = {"title": "", "items": []}
                payload["sections"].append(current_section)
            current_section["items"].append(_clean_release_note_item_text(item_match.group(1)))
    return payload if any(section["items"] for section in payload["sections"]) else None


def parse_remote_release_notes(markdown: str) -> list[dict]:
    headings = list(CHANGELOG_HEADING_PATTERN.finditer(markdown))
    release_notes: list[dict] = []
    for index, heading in enumerate(headings):
        version = normalize_stable_version(heading.group(1))
        if version is None:
            continue
        next_heading = headings[index + 1] if index + 1 < len(headings) else None
        block_end = next_heading.start() if next_heading is not None else len(markdown)
        parsed = _parse_release_notes_block(version, markdown[heading.end() : block_end])
        if parsed is not None:
            release_notes.append(parsed)
    return release_notes


def _parse_github_release_notes(release_payload: dict, latest_version: str) -> dict | None:
    body = release_payload.get("body")
    if not isinstance(body, str):
        return None
    parsed = _parse_release_notes_block(latest_version, body)
    if parsed is None:
        return None
    published_at = release_payload.get("published_at")
    if isinstance(published_at, str) and re.match(r"^\d{4}-\d{2}-\d{2}", published_at):
        parsed["date"] = published_at[:10]
    return parsed


def _valid_release_asset_url(url: str, tag_name: str, filename: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    expected_path = f"/frederikemmer/MediaLyze/releases/download/{tag_name}/{filename}"
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and parsed.path == expected_path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _parse_desktop_assets(release_payload: dict, tag_name: str) -> list[dict]:
    parsed_assets: list[dict] = []
    assets = release_payload.get("assets")
    if not isinstance(assets, list):
        return parsed_assets
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("state") != "uploaded":
            continue
        filename = asset.get("name")
        target = DESKTOP_ASSET_TARGETS.get(filename)
        size_bytes = asset.get("size")
        download_url = asset.get("browser_download_url")
        if (
            target is None
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes <= 0
            or not isinstance(download_url, str)
            or not _valid_release_asset_url(download_url, tag_name, filename)
        ):
            continue
        sha256 = None
        digest = asset.get("digest")
        if digest is not None:
            if not isinstance(digest, str):
                continue
            match = SHA256_DIGEST_PATTERN.fullmatch(digest)
            if match is None:
                continue
            sha256 = match.group(1).lower()
        platform, arch = target
        parsed_assets.append(
            {
                "platform": platform,
                "arch": arch,
                "filename": filename,
                "download_url": download_url,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        )
    return sorted(parsed_assets, key=lambda asset: (asset["platform"], asset["arch"]))


def _get_text(url: str, timeout_seconds: float) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json" if url.endswith("/latest") else "text/plain",
            "User-Agent": "MediaLyze-update-check",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
        return response.read().decode("utf-8")


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _stored_update_payload(db: Session) -> dict:
    stored = db.get(AppSetting, UPDATE_STATUS_KEY)
    return dict(stored.value) if stored is not None and isinstance(stored.value, dict) else {}


def get_update_status(
    db: Session,
    settings: Settings,
    *,
    automatic_reminder_eligible: bool | None = None,
) -> UpdateStatusRead:
    payload = _stored_update_payload(db)
    payload["checked_at"] = _parse_datetime(payload.get("checked_at"))
    payload["current_version"] = settings.app_version
    payload["update_available"] = is_newer_stable_version(payload.get("latest_version"), settings.app_version)
    if automatic_reminder_eligible is None:
        checked_at = payload.get("checked_at")
        automatic_reminder_eligible = bool(
            payload["update_available"]
            and checked_at is not None
            and utc_now() - checked_at <= UPDATE_STATUS_MAX_AGE
        )
    payload["automatic_reminder_eligible"] = automatic_reminder_eligible
    try:
        return UpdateStatusRead.model_validate(payload)
    except (TypeError, ValueError):
        return UpdateStatusRead(
            current_version=settings.app_version,
            automatic_reminder_eligible=False,
        )


def _asset_retry_due(payload: dict, status: UpdateStatusRead) -> bool:
    try:
        retry_count = int(payload.get("asset_retry_count", 0))
    except (TypeError, ValueError):
        retry_count = 0
    if (
        not status.update_available
        or len(status.desktop_assets) == len(DESKTOP_ASSET_TARGETS)
        or retry_count >= MISSING_ASSET_RETRY_LIMIT
    ):
        return False
    retry_after = _parse_datetime(payload.get("asset_retry_after"))
    if retry_after is None and status.checked_at is not None:
        retry_after = status.checked_at + MISSING_ASSET_RETRY_AGE
    return retry_after is None or utc_now() >= retry_after


def _is_update_status_stale(payload: dict, status: UpdateStatusRead) -> bool:
    if status.checked_at is None:
        return True
    if _asset_retry_due(payload, status):
        return True
    return utc_now() - status.checked_at > UPDATE_STATUS_MAX_AGE


def get_or_check_update_status(db: Session, settings: Settings) -> UpdateStatusRead:
    payload = _stored_update_payload(db)
    status = get_update_status(db, settings)
    if not _is_update_status_stale(payload, status):
        return status
    retry_due = _asset_retry_due(payload, status)
    refreshed = check_for_updates(db, settings)
    if refreshed is not None:
        return refreshed
    if retry_due:
        _record_failed_asset_retry(db, payload)
    return status.model_copy(update={"automatic_reminder_eligible": False})


def _record_failed_asset_retry(db: Session, payload: dict) -> None:
    stored = db.get(AppSetting, UPDATE_STATUS_KEY)
    if stored is None:
        return
    try:
        previous_retry_count = int(payload.get("asset_retry_count", 0))
    except (TypeError, ValueError):
        previous_retry_count = 0
    retry_count = min(previous_retry_count + 1, MISSING_ASSET_RETRY_LIMIT)
    next_payload = dict(payload)
    next_payload["asset_retry_count"] = retry_count
    next_payload["asset_retry_after"] = (
        (utc_now() + MISSING_ASSET_RETRY_AGE).isoformat()
        if retry_count < MISSING_ASSET_RETRY_LIMIT
        else None
    )
    stored.value = next_payload
    db.commit()


def check_for_updates(db: Session, settings: Settings) -> UpdateStatusRead | None:
    current_version = normalize_stable_version(settings.app_version)
    if current_version is None:
        return None

    timeout = getattr(settings, "telemetry_timeout_seconds", UPDATE_CHECK_TIMEOUT_SECONDS)
    try:
        release_payload = json.loads(_get_text(LATEST_RELEASE_URL, timeout))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(release_payload, dict):
        return None
    tag_name = release_payload.get("tag_name")
    latest_version = normalize_stable_version(tag_name if isinstance(tag_name, str) else None)
    if (
        release_payload.get("draft")
        or release_payload.get("prerelease")
        or latest_version is None
        or not isinstance(tag_name, str)
        or tag_name != f"v{latest_version}"
    ):
        return None

    try:
        supplemental_release_notes = parse_remote_release_notes(
            _get_text(REMOTE_CHANGELOG_URL, timeout)
        )
    except (OSError, ValueError):
        supplemental_release_notes = []

    release_notes_by_version = {
        notes["version"]: notes for notes in supplemental_release_notes
    }
    latest_release_notes = _parse_github_release_notes(release_payload, latest_version)
    if latest_release_notes is not None:
        release_notes_by_version[latest_version] = latest_release_notes
    elif latest_version not in release_notes_by_version:
        release_notes_by_version[latest_version] = {
            "version": latest_version,
            "date": None,
            "sections": [],
        }
    release_notes = sorted(
        release_notes_by_version.values(),
        key=lambda notes: semver_key(notes["version"]),
        reverse=True,
    )
    desktop_assets = _parse_desktop_assets(release_payload, tag_name)

    checked_at = utc_now()
    previous_payload = _stored_update_payload(db)
    same_release = previous_payload.get("latest_version") == latest_version
    try:
        previous_retry_count = int(previous_payload.get("asset_retry_count", 0)) if same_release else 0
    except (TypeError, ValueError):
        previous_retry_count = 0
    previous_desktop_assets = previous_payload.get("desktop_assets")
    previous_asset_count = len(previous_desktop_assets) if isinstance(previous_desktop_assets, list) else 0
    retried_same_release = (
        same_release
        and previous_asset_count < len(DESKTOP_ASSET_TARGETS)
        and _asset_retry_due(previous_payload, get_update_status(db, settings))
    )
    asset_retry_count = min(
        previous_retry_count + (1 if retried_same_release else 0),
        MISSING_ASSET_RETRY_LIMIT,
    )
    assets_complete = len(desktop_assets) == len(DESKTOP_ASSET_TARGETS)
    payload = {
        "latest_version": latest_version,
        "latest_release_url": f"{RELEASE_PAGE_BASE}/{tag_name}",
        "checked_at": checked_at.isoformat(),
        "release_notes": release_notes,
        "desktop_assets": desktop_assets,
        "asset_retry_count": 0 if assets_complete else asset_retry_count,
        "asset_retry_after": (
            None
            if assets_complete or asset_retry_count >= MISSING_ASSET_RETRY_LIMIT
            else (checked_at + MISSING_ASSET_RETRY_AGE).isoformat()
        ),
    }
    stored = db.get(AppSetting, UPDATE_STATUS_KEY)
    if stored is None:
        db.add(AppSetting(key=UPDATE_STATUS_KEY, value=payload))
    else:
        stored.value = payload
    db.commit()
    return get_update_status(db, settings)


def get_desktop_update_reminder(db: Session) -> DesktopUpdateReminderRead:
    stored = db.get(AppSetting, DESKTOP_UPDATE_REMINDER_KEY)
    payload = dict(stored.value) if stored is not None and isinstance(stored.value, dict) else {}
    version = normalize_stable_version(payload.get("version"))
    reminded_at = _parse_datetime(payload.get("reminded_at"))
    if reminded_at is not None and reminded_at > utc_now():
        reminded_at = None
    if version is not None and reminded_at is not None:
        return DesktopUpdateReminderRead(version=version, reminded_at=reminded_at)
    if stored is not None and stored.value:
        stored.value = {}
        db.commit()
    return DesktopUpdateReminderRead()


def mark_desktop_update_reminder(
    db: Session,
    settings: Settings,
    version: str,
) -> DesktopUpdateReminderRead:
    normalized_version = normalize_stable_version(version)
    status = get_update_status(db, settings)
    if (
        normalized_version is None
        or not status.update_available
        or normalized_version != status.latest_version
    ):
        raise ValueError("Version is not the currently available stable update")
    reminded_at = utc_now()
    payload = {"version": normalized_version, "reminded_at": reminded_at.isoformat()}
    stored = db.get(AppSetting, DESKTOP_UPDATE_REMINDER_KEY)
    if stored is None:
        db.add(AppSetting(key=DESKTOP_UPDATE_REMINDER_KEY, value=payload))
    else:
        stored.value = payload
    db.commit()
    return DesktopUpdateReminderRead(version=normalized_version, reminded_at=reminded_at)
