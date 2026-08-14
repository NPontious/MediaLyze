from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ConnectorServerInfo:
    name: str | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorConfigurationField:
    key: str
    input_type: str = "text"
    required: bool = False
    secret: bool = False


@dataclass(frozen=True, slots=True)
class ConnectorProviderDescriptor:
    provider: str
    configuration_fields: tuple[ConnectorConfigurationField, ...] = ()
    optional_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RemoteLocation:
    path: str


@dataclass(frozen=True, slots=True)
class RemoteLibrary:
    remote_id: str
    name: str
    media_type: str | None = None
    locations: tuple[RemoteLocation, ...] = ()
    provider_payload: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemoteItem:
    remote_id: str
    item_type: str
    title: str
    library_remote_id: str | None = None
    remote_path: str | None = None
    original_title: str | None = None
    series_name: str | None = None
    season_name: str | None = None
    index_number: int | None = None
    parent_index_number: int | None = None
    date_created: datetime | None = None
    premiere_date: datetime | None = None
    production_year: int | None = None
    overview: str | None = None
    provider_ids: dict = field(default_factory=dict)
    size_bytes: int | None = None
    duration_seconds: float | None = None
    provider_payload: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemoteUser:
    remote_id: str
    name: str


@dataclass(frozen=True, slots=True)
class RemoteUserItemData:
    item_remote_id: str
    user_remote_id: str
    play_count: int = 0
    played: bool = False
    playback_position_ticks: int = 0
    last_played_date: datetime | None = None
    is_favorite: bool = False


@dataclass(frozen=True, slots=True)
class RemotePlaybackEvent:
    remote_event_id: str
    item_remote_id: str
    user_remote_id: str
    played_at: datetime


class ConnectorAdapter(Protocol):
    provider: str
    capabilities: frozenset[str]

    def __enter__(self) -> "ConnectorAdapter": ...

    def __exit__(self, *_args: object) -> None: ...

    def test_connection(self) -> ConnectorServerInfo: ...

    def get_server_info(self) -> ConnectorServerInfo: ...

    def iter_libraries(self) -> Iterable[RemoteLibrary]: ...

    def iter_items(self, libraries: Iterable[RemoteLibrary]) -> Iterator[RemoteItem]: ...

    def iter_users(self) -> Iterable[RemoteUser]: ...

    def iter_user_item_data(self, users: Iterable[RemoteUser]) -> Iterator[RemoteUserItemData]: ...

    def iter_playback_events(
        self,
        users: Iterable[RemoteUser],
        *,
        min_date: datetime | None = None,
    ) -> Iterator[RemotePlaybackEvent]: ...
