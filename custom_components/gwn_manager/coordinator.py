"""Data update coordinator for the GWN Manager integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GWNAuthError, GWNClient, GWNError
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_SERVER_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

type GWNConfigEntry = ConfigEntry[GWNCoordinator]

# The live client list only counts the bytes of the current session; keep the
# accumulated values of the full client list for these.
HISTORIC_KEYS = {"txBytes", "rxBytes", "totalBytes"}


@dataclass
class GWNData:
    """Snapshot of everything fetched in one update."""

    # Keyed by upper-case MAC address.
    aps: dict[str, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse the different timestamp formats the API returns."""
    if not value or not isinstance(value, str):
        return None
    # ISO 8601, e.g. "2026-10-24T09:19:28.000+0000"
    if (parsed := dt_util.parse_datetime(value)) is not None:
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.get_default_time_zone())
    # "2026-10-24 02:52" in the GWN Manager's (assumed: Home Assistant's) time zone
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt_util.get_default_time_zone())


class GWNCoordinator(DataUpdateCoordinator[GWNData]):
    """Polls APs and clients of all networks."""

    config_entry: GWNConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GWNConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = GWNClient(
            async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
            entry.data[CONF_SERVER_URL],
            entry.data[CONF_APP_ID],
            entry.data[CONF_APP_SECRET],
        )

    async def _async_update_data(self) -> GWNData:
        try:
            return await self._fetch()
        except GWNAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GWNError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> GWNData:
        data = GWNData()
        for network in await self.client.get_networks():
            network_id = network["id"]
            for ap in await self.client.get_access_points(network_id):
                if mac := (ap.get("mac") or "").upper():
                    data.aps[mac] = {**ap, "networkName": network.get("networkName")}

            # All known clients (incl. offline) ...
            for client in await self.client.get_clients(network_id, connected_only=False):
                if mac := (client.get("clientId") or "").upper():
                    data.clients[mac] = {**client, "connected": False}
            # ... overlaid with the live values of the connected ones.
            for client in await self.client.get_clients(network_id, connected_only=True):
                if mac := (client.get("clientId") or "").upper():
                    merged = data.clients.get(mac, {})
                    merged.update(
                        {
                            k: v
                            for k, v in client.items()
                            if v not in (None, "") and k not in HISTORIC_KEYS
                        }
                    )
                    merged["connected"] = True
                    data.clients[mac] = merged
        return data
