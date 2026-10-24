"""Base entities for the GWN Manager integration."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SERVER_URL,
    CONF_SSID_FILTER,
    CONF_TRACK_OFFLINE,
    DEFAULT_TRACK_OFFLINE,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import GWNConfigEntry, GWNCoordinator


def client_name(mac: str, client: dict[str, Any]) -> str:
    """Return the best available display name of a client."""
    for key in ("name", "clientName", "dhcpHostname"):
        value = client.get(key)
        if value and value.upper() != mac:
            return value
    return mac


@callback
def async_get_device(hass: HomeAssistant, entry_id: str, mac: str) -> dr.DeviceEntry | None:
    """Return the device of this config entry for a client or AP MAC."""
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, mac), entry_id)


@callback
def async_client_area(
    hass: HomeAssistant, entry_id: str, client: dict[str, Any]
) -> str | None:
    """Return the area of the AP a connected client is associated with."""
    if not client.get("connected") or not (ap_mac := (client.get("apId") or "").upper()):
        return None
    device = async_get_device(hass, entry_id, ap_mac)
    if device is None or device.area_id is None:
        return None
    area = ar.async_get(hass).async_get_area(device.area_id)
    return area.name if area else None


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
    ssid_filter = set(entry.options.get(CONF_SSID_FILTER) or [])
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            mac
            for mac, client in coordinator.data.clients.items()
            if mac not in known
            and (track_offline or client["connected"])
            and (not ssid_filter or client.get("ssid") in ssid_filter)
        ]
        known.update(new)
        async_add_entities(entity for mac in new for entity in factory(coordinator, mac))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


@callback
def async_setup_port_entities(
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[GWNCoordinator, str, str], Iterable[Entity]],
) -> None:
    """Add entities for every switch port (identified by switch MAC and port ID)."""
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        new = [
            (mac, str(port["portId"]))
            for mac, ap in coordinator.data.aps.items()
            for port in ap.get("ports") or []
            if (mac, str(port["portId"])) not in known
        ]
        known.update(new)
        async_add_entities(
            entity for mac, port_id in new for entity in factory(coordinator, mac, port_id)
        )

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


@callback
def async_setup_ssid_entities(
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[GWNCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every SSID, now and whenever a new one shows up."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [key for key in coordinator.data.ssids if key not in known]
        known.update(new)
        async_add_entities(entity for key in new for entity in factory(coordinator, key))

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


class GWNPortEntity(GWNAPEntity):
    """An entity of a switch port, attached to the switch device."""

    def __init__(self, coordinator: GWNCoordinator, mac: str, port_id: str, key: str) -> None:
        super().__init__(coordinator, mac, f"port_{port_id}_{key}")
        self._port_id = port_id
        self._attr_translation_placeholders = {
            "port": str(self.port.get("silkScreenPort") or int(port_id) + 1)
        }

    @property
    def port(self) -> dict[str, Any]:
        """Return the latest data of this port."""
        for port in self.ap.get("ports") or []:
            if str(port.get("portId")) == self._port_id:
                return port
        return {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.port)


class GWNSSIDEntity(CoordinatorEntity[GWNCoordinator]):
    """An entity belonging to an SSID (wireless network)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GWNCoordinator, key: str, entity_key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_ssid_{key}_{entity_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ssid_{coordinator.config_entry.entry_id}_{key}")},
            name=self.ssid.get("ssidName"),
            manufacturer=MANUFACTURER,
            model="SSID",
            entry_type=dr.DeviceEntryType.SERVICE,
            configuration_url=coordinator.config_entry.data[CONF_SERVER_URL],
        )

    @property
    def ssid(self) -> dict[str, Any]:
        """Return the latest data of this SSID."""
        return self.coordinator.data.ssids.get(self._key, {})

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data.ssids
