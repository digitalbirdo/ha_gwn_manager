"""The GWN Manager integration."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import GWNConfigEntry, GWNCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> bool:
    """Set up GWN Manager from a config entry."""
    coordinator = GWNCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> None:
    """Reload after the options changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GWNConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing devices that the GWN Manager no longer reports."""
    data = entry.runtime_data.data
    return not any(
        identifier[0] == DOMAIN
        and (identifier[1] in data.clients or identifier[1] in data.aps)
        for identifier in device.identifiers
    )
