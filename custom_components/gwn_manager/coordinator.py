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
    EVENT_CLIENT_CONNECTED,
    EVENT_CLIENT_DISCONNECTED,
    EVENT_CLIENT_ROAMED,
    THROUGHPUT_IDLE_AFTER,
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
    # Keyed by "<network id>_<ssid id>".
    ssids: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Connection events detected in this update: (MAC, event type, event data).
    events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)


def normalize_mac(mac: str | None) -> str:
    """Return "AA:BB:CC:DD:EE:FF" for MACs with or without separators."""
    raw = (mac or "").replace(":", "").replace("-", "").upper()
    return ":".join(raw[i : i + 2] for i in range(0, len(raw), 2)) if len(raw) == 12 else raw


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
        # MAC -> (tx bytes, rx bytes, time of last change) for throughput
        self._byte_samples: dict[str, tuple[int, int, datetime]] = {}
        # MAC -> (upload bit/s, download bit/s)
        self._rates: dict[str, tuple[float, float]] = {}
        # MAC -> AP MAC of connected clients after the previous update
        self._previous: dict[str, str | None] | None = None
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

        data = GWNData(aps=self._full.aps, networks=self._full.networks, ssids=self._full.ssids)
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
                self._update_throughput(mac, client, now)

        for mac, client in data.clients.items():
            client["lastConnected"] = self._last_connected.get(mac)
            rates = self._rates.get(mac) if client["connected"] else None
            client["uploadRate"], client["downloadRate"] = rates or (None, None)

        self._detect_events(data)
        return data

    def _update_throughput(self, mac: str, client: dict[str, Any], now: datetime) -> None:
        """Derive bit/s from the session byte counters of a connected client."""
        try:
            tx, rx = int(client["txBytes"]), int(client["rxBytes"])
        except (KeyError, TypeError, ValueError):
            return
        previous = self._byte_samples.get(mac)
        if previous is None or tx < previous[0] or rx < previous[1]:
            # First sample or a new session: no rate yet.
            self._byte_samples[mac] = (tx, rx, now)
            return
        prev_tx, prev_rx, since = previous
        elapsed = (now - since).total_seconds()
        if (tx, rx) != (prev_tx, prev_rx) and elapsed > 0:
            # "tx" is the client's upload, "rx" its download.
            self._rates[mac] = ((tx - prev_tx) * 8 / elapsed, (rx - prev_rx) * 8 / elapsed)
            self._byte_samples[mac] = (tx, rx, now)
        elif elapsed > THROUGHPUT_IDLE_AFTER:
            self._rates[mac] = (0.0, 0.0)

    def _detect_events(self, data: GWNData) -> None:
        """Fire connect, disconnect and roaming events compared to the last update."""
        current = {
            mac: (client.get("apId") or "").upper() or None
            for mac, client in data.clients.items()
            if client["connected"]
        }
        previous, self._previous = self._previous, current
        if previous is None:
            # First update after setup: nothing to compare with.
            return

        def event_data(mac: str, ap_mac: str | None) -> dict[str, Any]:
            client = data.clients.get(mac, {})
            return {
                "mac": mac,
                "name": client.get("name") or client.get("dhcpHostname") or mac,
                "ip": client.get("ipv4") or None,
                "ssid": client.get("ssid"),
                "ap_mac": ap_mac,
                "ap_name": data.aps.get(ap_mac or "", {}).get("name"),
            }

        for mac, ap_mac in current.items():
            if mac not in previous:
                data.events.append((mac, "connected", event_data(mac, ap_mac)))
            elif previous[mac] != ap_mac:
                roamed = event_data(mac, ap_mac)
                roamed["previous_ap_mac"] = previous[mac]
                roamed["previous_ap_name"] = data.aps.get(previous[mac] or "", {}).get("name")
                data.events.append((mac, "roamed", roamed))
        for mac, ap_mac in previous.items():
            if mac not in current:
                data.events.append((mac, "disconnected", event_data(mac, ap_mac)))

        bus_events = {
            "connected": EVENT_CLIENT_CONNECTED,
            "disconnected": EVENT_CLIENT_DISCONNECTED,
            "roamed": EVENT_CLIENT_ROAMED,
        }
        for _, event_type, payload in data.events:
            self.hass.bus.async_fire(bus_events[event_type], payload)

    async def _fetch_device_extras(
        self, network_id: int, aps: dict[str, dict[str, Any]]
    ) -> None:
        """Add device type, switch ports and firmware info to the devices."""
        infos = await asyncio.gather(
            *(self.client.get_device_info(network_id, mac) for mac in aps),
            return_exceptions=True,
        )
        for (mac, ap), info in zip(aps.items(), infos, strict=True):
            if isinstance(info, GWNAuthError):
                raise info
            ap["info"] = {} if isinstance(info, BaseException) else info
            ap["deviceType"] = ap["info"].get("deviceType")
            if ap["deviceType"] == "SWITCH":
                try:
                    ap["ports"] = await self.client.get_switch_ports(network_id, mac)
                except GWNAuthError:
                    raise
                except GWNError as err:
                    _LOGGER.debug("No ports for %s: %s", mac, err)
                    ap["ports"] = []

        try:
            firmware = await self.client.get_firmware_versions(network_id)
        except GWNAuthError:
            raise
        except GWNError as err:
            _LOGGER.debug("No firmware info: %s", err)
            firmware = []
        for item in firmware:
            if (ap := aps.get(normalize_mac(item.get("mac")))) is not None:
                ap["firmwareLatest"] = item.get("lastVersion")

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
            await self._fetch_device_extras(network_id, network_aps)
            data.aps.update(network_aps)

            for ssid in await self.client.get_ssids(network_id):
                try:
                    config = await self.client.get_ssid_configuration(ssid["id"])
                except GWNAuthError:
                    raise
                except GWNError as err:
                    _LOGGER.debug("No configuration for SSID %s: %s", ssid.get("ssidName"), err)
                    config = {}
                data.ssids[f"{network_id}_{ssid['id']}"] = {
                    **ssid,
                    "networkId": network_id,
                    "configuration": config.get("configuration") or {},
                }

            # All known clients, including offline ones, without live values.
            for client in await self.client.get_clients(network_id, connected_only=False):
                if mac := (client.get("clientId") or "").upper():
                    data.clients[mac] = {**client, "timeZone": time_zone}
        return data
