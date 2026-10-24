"""Buttons for GWN Manager devices."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import GWNError
from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import GWNAPEntity, async_setup_ap_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GWNConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a restart button for every device."""
    async_setup_ap_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [GWNRestartButton(coordinator, mac)],
    )


class GWNRestartButton(GWNAPEntity, ButtonEntity):
    """Reboot an access point, switch or router."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "restart")

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.reboot_device(self._mac)
        except GWNError as err:
            raise HomeAssistantError(f"Could not restart {self.ap.get('name')}: {err}") from err
