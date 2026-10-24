"""Binary sensors for GWN Manager access points."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import GWNAPEntity, GWNPortEntity, async_setup_ap_entities, async_setup_port_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the online status of every access point."""
    async_setup_ap_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [GWNAPOnlineSensor(coordinator, mac)],
    )

    def port_entities(coordinator: GWNCoordinator, mac: str, port_id: str) -> list:
        entities: list = [GWNPortLinkSensor(coordinator, mac, port_id)]
        if coordinator.data.aps[mac].get("info", {}).get("isSupportPoe"):
            entities.append(GWNPortPoESensor(coordinator, mac, port_id))
        return entities

    async_setup_port_entities(entry, async_add_entities, port_entities)


class GWNAPOnlineSensor(GWNAPEntity, BinarySensorEntity):
    """Whether the GWN Manager sees the access point as online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "online")

    @property
    def is_on(self) -> bool:
        return self.ap.get("status") == 1


class GWNPortLinkSensor(GWNPortEntity, BinarySensorEntity):
    """Whether a device is connected to the switch port."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "port_link"

    def __init__(self, coordinator: GWNCoordinator, mac: str, port_id: str) -> None:
        super().__init__(coordinator, mac, port_id, "link")

    @property
    def is_on(self) -> bool:
        return self.port.get("operStatus") == "up"


class GWNPortPoESensor(GWNPortEntity, BinarySensorEntity):
    """Whether the switch port supplies PoE power."""

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_translation_key = "port_poe"

    def __init__(self, coordinator: GWNCoordinator, mac: str, port_id: str) -> None:
        super().__init__(coordinator, mac, port_id, "poe")

    @property
    def is_on(self) -> bool:
        return self.port.get("powerflag") == "on"
