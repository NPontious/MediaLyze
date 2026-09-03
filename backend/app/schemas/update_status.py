from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.schemas._time import UtcDateTime


class UpdateReleaseNotesSectionRead(BaseModel):
    title: str
    items: list[str]


class UpdateReleaseNotesRead(BaseModel):
    version: str
    date: str | None = None
    sections: list[UpdateReleaseNotesSectionRead]


class UpdateDesktopAssetRead(BaseModel):
    platform: Literal["darwin", "win32", "linux"]
    arch: Literal["arm64", "x64"]
    filename: str
    download_url: str
    size_bytes: int = Field(gt=0)
    sha256: str | None = None


class UpdateStatusRead(BaseModel):
    current_version: str
    latest_version: str | None = None
    latest_release_url: str | None = None
    update_available: bool = False
    automatic_reminder_eligible: bool = False
    checked_at: UtcDateTime | None = None
    release_notes: list[UpdateReleaseNotesRead] = Field(default_factory=list)
    desktop_assets: list[UpdateDesktopAssetRead] = Field(default_factory=list)


class DesktopUpdateReminderRead(BaseModel):
    version: str | None = None
    reminded_at: UtcDateTime | None = None


class DesktopUpdateReminderMark(BaseModel):
    version: str
