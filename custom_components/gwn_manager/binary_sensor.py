"""Binary sensors for GWN Manager access points."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import GWNAPEntity, async_setup_ap_entities

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


class GWNAPOnlineSensor(GWNAPEntity, BinarySensorEntity):
    """Whether the GWN Manager sees the access point as online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "online")

    @property
    def is_on(self) -> bool:
        return self.ap.get("status") == 1
