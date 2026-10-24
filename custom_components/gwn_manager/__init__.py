"""The Grandstream GWN Manager integration."""
from __future__ import annotations

from datetime import timedelta
import logging
from .gwn_manager_api import GWNClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.reload import async_setup_reload_service

from .api import GWNManagerAPI
from .const import CONF_APP_ID, CONF_APP_SECRET, CONF_SERVER_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Grandstream GWN Manager from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    #api = GWNManagerAPI(
    #    server_url=entry.data[CONF_SERVER_URL],
    #    app_id=entry.data[CONF_APP_ID],
    #    app_secret=entry.data[CONF_APP_SECRET],
    #)

    client = GWNClient(entry.data[CONF_APP_ID], entry.data[CONF_APP_SECRET], entry.data[CONF_SERVER_URL])
    client.authenticate()

    async def async_update_data():
        """Fetch data from API endpoint."""
        try:
            # Note: In a real scenario, you might want to handle authentication expiration here
            return await client.get_data()
           
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=10),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
