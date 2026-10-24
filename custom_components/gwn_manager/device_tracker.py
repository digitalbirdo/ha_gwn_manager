"""Device tracker (presence) for GWN Manager clients."""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import async_setup_client_entities, client_name

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a tracker for every client."""
    async_setup_client_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [GWNClientTracker(coordinator, mac)],
    )


class GWNClientTracker(CoordinatorEntity[GWNCoordinator], ScannerEntity):
    """Home/away state of a client based on its Wi-Fi association.

    ScannerEntity attaches itself to the device with the client's MAC, which
    is the client device created by the sensor platform.
    """

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_mac_address = mac
        self._attr_name = client_name(mac, self.client)

    @property
    def client(self) -> dict[str, Any]:
        return self.coordinator.data.clients.get(self._mac, {})

    @property
    def entity_registry_enabled_default(self) -> bool:
        # Presence is the main purpose of this integration, and the client
        # device always exists, so don't apply ScannerEntity's "unknown
        # device" logic.
        return True

    @property
    def is_connected(self) -> bool:
        return bool(self.client.get("connected"))

    @property
    def ip_address(self) -> str | None:
        return self.client.get("ipv4") or None

    @property
    def hostname(self) -> str | None:
        return self.client.get("dhcpHostname") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self.client
        return {
            "access_point": client.get("apName"),
            "ssid": client.get("ssid"),
        }
