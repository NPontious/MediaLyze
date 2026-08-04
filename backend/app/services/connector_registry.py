from __future__ import annotations

from collections.abc import Callable

from backend.app.models.entities import ConnectorConnection
from backend.app.services.connector_contract import ConnectorAdapter


AdapterFactory = Callable[[ConnectorConnection, str, object | None], ConnectorAdapter]


class ConnectorProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, provider: str, factory: AdapterFactory) -> None:
        key = provider.strip().casefold()
        if not key:
            raise ValueError("Connector provider must not be empty")
        self._factories[key] = factory

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def create(
        self,
        connection: ConnectorConnection,
        secret: str,
        cancellation_check: object | None = None,
    ) -> ConnectorAdapter:
        provider = connection.provider.strip().casefold()
        factory = self._factories.get(provider)
        if factory is None:
            raise ValueError(f"Unsupported connector provider: {connection.provider}")
        return factory(connection, secret, cancellation_check)


connector_registry = ConnectorProviderRegistry()


def register_builtin_connectors() -> None:
    from backend.app.services.jellyfin_connector import create_jellyfin_connector

    connector_registry.register("jellyfin", create_jellyfin_connector)


register_builtin_connectors()
