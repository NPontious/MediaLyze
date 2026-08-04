from __future__ import annotations

from collections.abc import Callable

from backend.app.models.entities import ConnectorConnection
from backend.app.services.connector_contract import (
    ConnectorAdapter,
    ConnectorProviderDescriptor,
)


AdapterFactory = Callable[[ConnectorConnection, str, object | None], ConnectorAdapter]


class ConnectorProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}
        self._descriptors: dict[str, ConnectorProviderDescriptor] = {}

    def register(
        self,
        provider: str,
        factory: AdapterFactory,
        *,
        descriptor: ConnectorProviderDescriptor | None = None,
    ) -> None:
        key = provider.strip().casefold()
        if not key:
            raise ValueError("Connector provider must not be empty")
        self._factories[key] = factory
        self._descriptors[key] = descriptor or ConnectorProviderDescriptor(provider=key)

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def descriptors(self) -> tuple[ConnectorProviderDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))

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
    from backend.app.services.connector_contract import (
        ConnectorConfigurationField,
        ConnectorProviderDescriptor,
    )
    from backend.app.services.jellyfin_connector import create_jellyfin_connector

    connector_registry.register(
        "jellyfin",
        create_jellyfin_connector,
        descriptor=ConnectorProviderDescriptor(
            provider="jellyfin",
            configuration_fields=(
                ConnectorConfigurationField("base_url", input_type="url", required=True),
                ConnectorConfigurationField(
                    "secret",
                    input_type="password",
                    required=True,
                    secret=True,
                ),
            ),
            optional_capabilities=("users", "user_states", "playback_events", "images"),
        ),
    )


register_builtin_connectors()
