"""The GWN Manager integration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import GWNConfigEntry, GWNCoordinator
from .entity import async_get_device
from .fast.manager import FastSources
from .model_image import GWNModelImageView
from .websocket import async_register_websocket

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FRONTEND_DIR = Path(__file__).parent / "frontend"
CARDS = ("gwn-network-card.js", "gwn-topology-card.js", "gwn-client-detail.js")

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the network map card and register its WebSocket API."""
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(f"/gwn_manager/{card}", str(FRONTEND_DIR / card), cache_headers=False)
            for card in CARDS
        ]
    )
    hass.http.register_view(GWNModelImageView(hass))
    await _async_register_cards(hass, version)
    async_register_websocket(hass)
    return True


async def _async_register_cards(hass: HomeAssistant, version: str) -> None:
    """Make the cards available in the frontend.

    Registers them as dashboard resources (what custom cards normally use, and
    what the mobile apps reliably load), keeping the version query current to
    bust caches. Falls back to an extra frontend module for YAML resources.
    """
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != "storage":
        for card in CARDS:
            add_extra_js_url(hass, f"/gwn_manager/{card}?v={version}")
        return

    resources = lovelace.resources
    await resources.async_get_info()  # loads the stored resources
    for card in CARDS:
        path = f"/gwn_manager/{card}"
        url = f"{path}?v={version}"
        existing = [item for item in resources.async_items() if item["url"].split("?")[0] == path]
        if not existing:
            await resources.async_create_item({"res_type": "module", "url": url})
            continue
        first, *duplicates = existing
        if first["url"] != url:
            await resources.async_update_item(first["id"], {"res_type": "module", "url": url})
        for item in duplicates:
            await resources.async_delete_item(item["id"])


async def async_setup_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> bool:
    """Set up GWN Manager from a config entry."""
    coordinator = GWNCoordinator(hass, entry)
    await coordinator.async_load_removed()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _update_via_devices() -> None:
        _async_link_clients_to_aps(hass, coordinator)

    # The platforms have created all devices by now.
    _update_via_devices()
    entry.async_on_unload(coordinator.async_add_listener(_update_via_devices))

    @callback
    def _remove_stale_clients() -> None:
        _async_remove_stale_clients(hass, coordinator)

    _remove_stale_clients()
    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_clients))
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    coordinator.fast_sources = FastSources(hass, entry, coordinator)
    await coordinator.fast_sources.async_start()
    entry.async_on_unload(coordinator.fast_sources.async_stop)
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


@callback
def _async_remove_stale_clients(hass: HomeAssistant, coordinator: GWNCoordinator) -> None:
    """Remove the devices of clients not connected for longer than the option allows."""
    now = dt_util.utcnow()
    stale = {
        mac
        for mac, client in coordinator.data.clients.items()
        if mac not in coordinator.removed and coordinator.is_stale(mac, client, now)
    }
    if not stale:
        return
    dev_reg = dr.async_get(hass)
    entry_id = coordinator.config_entry.entry_id
    for mac in stale:
        if device := async_get_device(hass, entry_id, mac):
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry_id)
    coordinator.async_mark_removed(stale)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GWNConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing devices: clients that aren't connected, anything no longer reported.

    A removed client gets no entities again until it connects again.
    """
    coordinator = entry.runtime_data
    data = coordinator.data
    macs = {identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN}
    if any(data.clients.get(mac, {}).get("connected") for mac in macs):
        return False
    ssids = {f"ssid_{entry.entry_id}_{key}" for key in data.ssids}
    if any(mac in data.aps or mac in ssids for mac in macs):
        return False
    coordinator.async_mark_removed({mac for mac in macs if mac in data.clients} or macs)
    return True
