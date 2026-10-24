"""Data update coordinator for the GWN Manager integration."""
from __future__ import annotations

import asyncio
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
    CONF_PRESENCE_INTERVAL,
    CONF_SERVER_URL,
    DEFAULT_PRESENCE_INTERVAL,
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
    # Network ID -> time zone name; filled by the full update.
    networks: dict[int, str | None] = field(default_factory=dict)


def parse_timestamp(value: Any, time_zone: str | None = None) -> datetime | None:
    """Parse the different timestamp formats the API returns.

    Timestamps without offset are in the time zone of the GWN network.
    """
    if not value or not isinstance(value, str):
        return None
    tzinfo = (time_zone and dt_util.get_time_zone(time_zone)) or dt_util.get_default_time_zone()
    # ISO 8601, e.g. "2026-10-24T09:19:28.000+0000"
    if (parsed := dt_util.parse_datetime(value)) is None:
        # e.g. "2026-10-24 02:52"
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tzinfo)


class GWNCoordinator(DataUpdateCoordinator[GWNData]):
    """Polls APs and clients of all networks.

    Runs at the presence interval and only fetches the connected clients then;
    everything else is refreshed when the (longer) scan interval has passed.
    """

    config_entry: GWNConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GWNConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_PRESENCE_INTERVAL, DEFAULT_PRESENCE_INTERVAL)
            ),
        )
        self._full_interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        self._full: GWNData | None = None
        self._full_fetched = datetime.min.replace(tzinfo=dt_util.UTC)
        self._last_connected: dict[str, datetime] = {}
        self.client = GWNClient(
            async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
            entry.data[CONF_SERVER_URL],
            entry.data[CONF_APP_ID],
            entry.data[CONF_APP_SECRET],
        )
        # Network time zones hardly ever change; fetch them once per setup.
        self._time_zones: dict[int, str | None] = {}

    async def _async_update_data(self) -> GWNData:
        try:
            return await self._fetch()
        except GWNAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GWNError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> GWNData:
        now = dt_util.utcnow()
        if self._full is None or now - self._full_fetched >= self._full_interval:
            self._full = await self._fetch_full()
            self._full_fetched = now

        data = GWNData(aps=self._full.aps, networks=self._full.networks)
        for mac, client in self._full.clients.items():
            data.clients[mac] = {**client, "connected": False}

        for network_id, time_zone in self._full.networks.items():
            for client in await self.client.get_clients(network_id, connected_only=True):
                if not (mac := (client.get("clientId") or "").upper()):
                    continue
                merged = data.clients.setdefault(mac, {"timeZone": time_zone})
                merged.update(
                    {
                        k: v
                        for k, v in client.items()
                        if v not in (None, "") and k not in HISTORIC_KEYS
                    }
                )
                merged["connected"] = True
                self._last_connected[mac] = now

        for mac, client in data.clients.items():
            client["lastConnected"] = self._last_connected.get(mac)
        return data

    async def _fetch_full(self) -> GWNData:
        """Fetch everything except the live client values."""
        _LOGGER.debug("Full update")
        data = GWNData()
        for network in await self.client.get_networks():
            network_id = network["id"]
            if network_id not in self._time_zones:
                detail = await self.client.get_network_detail(network_id)
                time_zone = detail.get("timezone")
                # Loads the zone info in the executor; later sync lookups in
                # parse_timestamp() hit the cache.
                if time_zone and await dt_util.async_get_time_zone(time_zone) is None:
                    time_zone = None
                self._time_zones[network_id] = time_zone
            time_zone = self._time_zones[network_id]
            data.networks[network_id] = time_zone

            network_aps: dict[str, dict[str, Any]] = {}
            for ap in await self.client.get_access_points(network_id):
                if mac := (ap.get("mac") or "").upper():
                    network_aps[mac] = {**ap, "networkName": network.get("networkName")}
            details = await asyncio.gather(
                *(self.client.get_device_details(mac) for mac in network_aps),
                return_exceptions=True,
            )
            for (mac, ap), detail in zip(network_aps.items(), details, strict=True):
                if isinstance(detail, GWNAuthError):
                    raise detail
                if isinstance(detail, BaseException):
                    # Optional data; don't fail the whole update for it.
                    _LOGGER.debug("No details for %s: %s", mac, detail)
                    detail = {}
                ap["details"] = detail
            data.aps.update(network_aps)

            # All known clients, including offline ones, without live values.
            for client in await self.client.get_clients(network_id, connected_only=False):
                if mac := (client.get("clientId") or "").upper():
                    data.clients[mac] = {**client, "timeZone": time_zone}
        return data
