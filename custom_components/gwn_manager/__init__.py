"""The GWN Manager integration."""
from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import async_get_device

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> bool:
    """Set up GWN Manager from a config entry."""
    coordinator = GWNCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _update_via_devices() -> None:
        _async_link_clients_to_aps(hass, coordinator)

    # The platforms have created all devices by now.
    _update_via_devices()
    entry.async_on_unload(coordinator.async_add_listener(_update_via_devices))
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


@callback
def _async_link_clients_to_aps(hass: HomeAssistant, coordinator: GWNCoordinator) -> None:
    """Set "connected via" of each client device to the AP it is associated with.

    Follows the client when it roams; offline clients keep their last AP.
    """
    dev_reg = dr.async_get(hass)
    entry_id = coordinator.config_entry.entry_id
    ap_devices = {mac: async_get_device(hass, entry_id, mac) for mac in coordinator.data.aps}

    for mac, client in coordinator.data.clients.items():
        if not client["connected"]:
            continue
        ap_device = ap_devices.get((client.get("apId") or "").upper())
        device = async_get_device(hass, entry_id, mac)
        if ap_device and device and device.via_device_id != ap_device.id:
            dev_reg.async_update_device(device.id, via_device_id=ap_device.id)


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
