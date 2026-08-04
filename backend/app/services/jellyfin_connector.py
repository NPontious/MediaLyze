from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime
from uuid import UUID

from backend.app.models.entities import ConnectorConnection
from backend.app.services.connector_contract import (
    ConnectorServerInfo,
    RemoteItem,
    RemoteLibrary,
    RemoteLocation,
)
from backend.app.services.jellyfin_client import JellyfinClient, JellyfinItemPage
from backend.app.services.jellyfin_matching import normalize_jellyfin_path


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _remote_id(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        return UUID(candidate).hex
    except ValueError:
        return candidate.casefold()


def _library_id(folder: dict) -> str:
    candidate = str(folder.get("ItemId") or "").strip()
    if candidate:
        return candidate
    locations = "|".join(sorted(str(value) for value in folder.get("Locations") or []))
    return f"legacy:{str(folder.get('Name') or '').strip()}:{locations}"


class JellyfinConnectorAdapter:
    provider = "jellyfin"
    capabilities = frozenset({"users", "user_states", "playback_events", "images"})

    def __init__(self, connection: ConnectorConnection, secret: str, cancellation_check=None) -> None:
        self.client = JellyfinClient(
            connection.base_url,
            secret,
            cancellation_check=cancellation_check if callable(cancellation_check) else None,
        )

    def __enter__(self) -> "JellyfinConnectorAdapter":
        self.client.__enter__()
        return self

    def __exit__(self, *_args: object) -> None:
        self.client.close()

    def test_connection(self) -> ConnectorServerInfo:
        return self.get_server_info()

    def get_server_info(self) -> ConnectorServerInfo:
        payload = self.client.get_system_info()
        return ConnectorServerInfo(
            name=str(payload.get("ServerName") or "").strip() or None,
            version=str(payload.get("Version") or "").strip() or None,
        )

    def iter_libraries(self) -> Iterable[RemoteLibrary]:
        for folder in self.client.get_virtual_folders():
            name = str(folder.get("Name") or "").strip()
            if not name:
                continue
            yield RemoteLibrary(
                remote_id=_library_id(folder),
                name=name,
                media_type=str(folder.get("CollectionType") or "").strip() or None,
                locations=tuple(
                    RemoteLocation(path=str(path))
                    for path in folder.get("Locations") or []
                    if str(path).strip()
                ),
                provider_payload={
                    "refresh_status": folder.get("RefreshStatus"),
                },
            )

    @staticmethod
    def _library_for_path(path: str | None, libraries: list[RemoteLibrary]) -> str | None:
        if not path:
            return None
        normalized = normalize_jellyfin_path(path)
        candidates: list[tuple[int, str]] = []
        for library in libraries:
            for location in library.locations:
                prefix = normalize_jellyfin_path(location.path)
                if normalized == prefix or normalized.startswith(f"{prefix}/"):
                    candidates.append((len(prefix), library.remote_id))
        return max(candidates, default=(0, None))[1]

    def iter_items(self, libraries: Iterable[RemoteLibrary]) -> Iterator[RemoteItem]:
        library_list = list(libraries)
        iterator = getattr(self.client, "iter_item_pages", None)
        if callable(iterator):
            pages = iterator()
        else:
            payloads = self.client.get_items()
            pages = [JellyfinItemPage(payloads, 0, len(payloads))]
        for page in pages:
            for payload in page.items:
                remote_id = _remote_id(payload.get("Id"))
                item_type = str(payload.get("Type") or "").strip()
                if not remote_id or not item_type:
                    continue
                path = str(payload.get("Path") or "").strip() or None
                ticks = payload.get("RunTimeTicks")
                duration = float(ticks) / 10_000_000 if isinstance(ticks, (int, float)) else None
                size = payload.get("Size")
                yield RemoteItem(
                    remote_id=remote_id,
                    library_remote_id=self._library_for_path(path, library_list),
                    item_type=item_type,
                    remote_path=path,
                    title=str(payload.get("Name") or "").strip(),
                    original_title=str(payload.get("OriginalTitle") or "").strip() or None,
                    series_name=str(payload.get("SeriesName") or "").strip() or None,
                    season_name=str(payload.get("SeasonName") or "").strip() or None,
                    index_number=payload.get("IndexNumber") if isinstance(payload.get("IndexNumber"), int) else None,
                    parent_index_number=(
                        payload.get("ParentIndexNumber")
                        if isinstance(payload.get("ParentIndexNumber"), int)
                        else None
                    ),
                    date_created=_parse_datetime(payload.get("DateCreated")),
                    premiere_date=_parse_datetime(payload.get("PremiereDate")),
                    production_year=(
                        payload.get("ProductionYear")
                        if isinstance(payload.get("ProductionYear"), int)
                        else None
                    ),
                    overview=str(payload.get("Overview") or "").strip() or None,
                    provider_ids=dict(payload.get("ProviderIds") or {}),
                    size_bytes=int(size) if isinstance(size, (int, float)) else None,
                    duration_seconds=duration,
                    provider_payload={
                        "image_tags": dict(payload.get("ImageTags") or {}),
                        "backdrop_image_tags": list(payload.get("BackdropImageTags") or []),
                    },
                )


def create_jellyfin_connector(
    connection: ConnectorConnection,
    secret: str,
    cancellation_check=None,
) -> JellyfinConnectorAdapter:
    return JellyfinConnectorAdapter(connection, secret, cancellation_check)
