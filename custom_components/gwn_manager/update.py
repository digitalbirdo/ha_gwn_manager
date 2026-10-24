"""Firmware updates for GWN Manager devices."""
from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
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
    """Set up a firmware update entity for every device."""
    async_setup_ap_entities(
        entry,
        async_add_entities,
        lambda coordinator, mac: [GWNFirmwareUpdate(coordinator, mac)],
    )


class GWNFirmwareUpdate(GWNAPEntity, UpdateEntity):
    """Firmware of a device compared to the version GWN Manager recommends."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: GWNCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac, "firmware_update")

    @property
    def installed_version(self) -> str | None:
        return self.ap.get("versionFirmware")

    @property
    def latest_version(self) -> str | None:
        return self.ap.get("firmwareLatest") or self.installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        # GWN Manager only installs its recommended version.
        try:
            await self.coordinator.client.upgrade_device(self._mac)
        except GWNError as err:
            raise HomeAssistantError(f"Could not start the update: {err}") from err
