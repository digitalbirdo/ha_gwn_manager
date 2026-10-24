"""Base entities for the GWN Manager integration."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERVER_URL, CONF_TRACK_OFFLINE, DEFAULT_TRACK_OFFLINE, DOMAIN, MANUFACTURER
from .coordinator import GWNConfigEntry, GWNCoordinator


def client_name(mac: str, client: dict[str, Any]) -> str:
    """Return the best available display name of a client."""
    for key in ("name", "clientName", "dhcpHostname"):
        value = client.get(key)
        if value and value.upper() != mac:
            return value
    return mac


@callback
def async_setup_ap_entities(
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[GWNCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every AP, now and whenever a new one shows up."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [mac for mac in coordinator.data.aps if mac not in known]
        known.update(new)
        async_add_entities(entity for mac in new for entity in factory(coordinator, mac))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


@callback
def async_setup_client_entities(
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[GWNCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every client, now and whenever a new one shows up."""
    coordinator = entry.runtime_data
    track_offline = entry.options.get(CONF_TRACK_OFFLINE, DEFAULT_TRACK_OFFLINE)
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            mac
            for mac, client in coordinator.data.clients.items()
            if mac not in known and (track_offline or client["connected"])
        ]
        known.update(new)
        async_add_entities(entity for mac in new for entity in factory(coordinator, mac))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class GWNAPEntity(CoordinatorEntity[GWNCoordinator]):
    """An entity belonging to an access point / switch / router."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GWNCoordinator, mac: str, key: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{mac}_{key}"
        ap = self.ap
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            name=ap.get("name") or mac,
            manufacturer=MANUFACTURER,
            model=ap.get("apType"),
            sw_version=ap.get("versionFirmware"),
            configuration_url=coordinator.config_entry.data[CONF_SERVER_URL],
        )

    @property
    def ap(self) -> dict[str, Any]:
        """Return the latest data of this AP."""
        return self.coordinator.data.aps.get(self._mac, {})

    @property
    def available(self) -> bool:
        return super().available and self._mac in self.coordinator.data.aps


class GWNClientEntity(CoordinatorEntity[GWNCoordinator]):
    """An entity belonging to a network client."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GWNCoordinator, mac: str, key: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            name=client_name(mac, self.client),
        )

    @property
    def client(self) -> dict[str, Any]:
        """Return the latest data of this client."""
        return self.coordinator.data.clients.get(self._mac, {})

    @property
    def available(self) -> bool:
        return super().available and self._mac in self.coordinator.data.clients
