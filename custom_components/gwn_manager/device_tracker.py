"""Device tracker (presence) for GWN Manager clients."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY

from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import async_client_area, async_setup_client_entities, client_name

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
        self._away_delay = timedelta(
            seconds=coordinator.config_entry.options.get(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY)
        )

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
        client = self.client
        if client.get("connected"):
            return True
        # Only report away once the client has been gone for the away delay.
        last_connected = client.get("lastConnected")
        return last_connected is not None and dt_util.utcnow() - last_connected < self._away_delay

    @property
    def ip_address(self) -> str | None:
        return self.client.get("ipv4") or None

    @property
    def hostname(self) -> str | None:
        return self.client.get("dhcpHostname") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self.client
        if not client.get("connected"):
            return {}
        return {
            "access_point": client.get("apName"),
            "area": async_client_area(
                self.hass, self.coordinator.config_entry.entry_id, client
            ),
            "ssid": client.get("ssid"),
        }
