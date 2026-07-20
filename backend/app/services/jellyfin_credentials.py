from __future__ import annotations

from pathlib import Path

from backend.app.models.entities import JellyfinConnection


def read_jellyfin_api_key(
    connection: JellyfinConnection | None,
    api_key_file: Path | None = None,
) -> str:
    if api_key_file is not None:
        try:
            secret = api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Could not read JELLYFIN_API_KEY_FILE: {exc}") from exc
        if secret:
            return secret
    return (connection.api_key if connection else "").strip()
