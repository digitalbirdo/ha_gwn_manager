"""Data update coordinator for the GWN Manager integration."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from itertools import permutations
from math import perm
from datetime import datetime, timedelta
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GWNAuthError, GWNClient, GWNError
from .changes import ChangeTracker
from .cleanup import is_stale
from .fast.state import FastState, apply_overlay, bssid_table

if TYPE_CHECKING:
    from .fast.manager import FastSources
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_MANUAL_UPLINKS,
    CONF_PRESENCE_INTERVAL,
    CONF_REMOVE_AFTER_DAYS,
    CONF_SERVER_URL,
    DEFAULT_PRESENCE_INTERVAL,
    DEFAULT_REMOVE_AFTER_DAYS,
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
# Band keys of the client lists in v2.0.0/ap/info -> channelClassStr of client/list
AP_CLIENT_BANDS = {"2g4": "2.4G", "5g": "5G", "6g": "6G"}


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
    # Client MAC -> connection as the APs report it (v2.0.0/ap/info); filled by
    # the full update.
    ap_clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Connection events detected in this update: (MAC, event type, event data).
    events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)


MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def parse_manual_uplinks(text: str | None) -> list[tuple[str, str, str | None]]:
    """Parse "client = device[:port]" lines into (client, device, port) tuples.

    Names and MACs are accepted on both sides. A target that is a bare MAC has
    no port; otherwise the part after the last ":" is the port.
    """
    result = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        client, target = (part.strip() for part in line.split("=", 1))
        if not client or not target:
            continue
        device, port = target, None
        if not MAC_RE.match(target) and ":" in target:
            head, _, tail = target.rpartition(":")
            if head.strip() and tail.strip():
                device, port = head.strip(), tail.strip()
        result.append((client, device, port))
    return result


def is_wired(client: dict[str, Any]) -> bool:
    """Whether a client is connected by cable.

    The "wired" flag of the API isn't reliable: GWN Manager also reports
    clients of an AP's Wi-Fi with wired=1 at times. Wi-Fi clients always
    have an SSID, a signal or a band.
    """
    return not (
        client.get("ssid") or client.get("rssi") is not None or client.get("channelClassStr")
    )


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
        # API snapshot of the last poll, before the fast sources are laid over it.
        self._base: GWNData | None = None
        # Syslog/SNMP state, set up in __init__.py when the options enable them.
        self.fast: FastState | None = None
        self.fast_max_age = timedelta(seconds=30)
        # Time of the last successful API poll, shown in the cards.
        self.last_api: datetime | None = None
        self.fast_sources: FastSources | None = None
        # Clients removed from Home Assistant (manually or as stale); they get
        # no entities until they connect again. Persisted per config entry.
        self.removed: set[str] = set()
        self._removed_store: Store[list[str]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.removed_clients"
        )
        # Last meaningful change per client, for the cards' flash.
        self.changes = ChangeTracker()
        # Wired clients seen connected since setup (MAC -> last record); the API
        # only lists wired clients while they are connected.
        self._wired: dict[str, dict[str, Any]] = {}
        # Clients the APs list as connected but client/list leaves out (a GWN
        # Manager bug); taken from the AP lists until the next full update.
        self._ap_only: set[str] = set()
        # Router MAC -> (WAN rx bytes, WAN tx bytes, time) for the WAN throughput
        self._wan_samples: dict[str, tuple[int, int, datetime]] = {}
        # Router MAC -> (download bit/s, upload bit/s)
        self._wan_rates: dict[str, tuple[float, float]] = {}
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
        full_fetched = False
        if self._full is None or now - self._full_fetched >= self._full_interval:
            self._full = await self._fetch_full()
            self._full_fetched = now
            full_fetched = True

        data = GWNData(aps=self._full.aps, networks=self._full.networks, ssids=self._full.ssids)
        for mac, client in self._full.clients.items():
            data.clients[mac] = {**client, "connected": False}
        for mac, client in self._wired.items():
            data.clients.setdefault(mac, {**client, "connected": False})

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
                # From here on "wired" is 1 for cable and 2 for Wi-Fi, see is_wired().
                merged["wired"] = 1 if is_wired(client) else 2
                if merged["wired"] == 1:
                    self._wired[mac] = {**merged}
                self._last_connected[mac] = now
                self._update_throughput(mac, client, now)

        self._apply_ap_clients(data, full_fetched, now)

        self._base = data
        self.last_api = now
        return self._compose(now, full_fetched)

    def _compose(self, now: datetime, full_fetched: bool = False) -> GWNData:
        """API snapshot plus the fast sources, with the derived values."""
        base = self._base
        assert base is not None
        data = GWNData(
            aps=base.aps,
            networks=base.networks,
            ssids=base.ssids,
            clients={mac: dict(client) for mac, client in base.clients.items()},
        )
        if self.fast is not None:
            if full_fetched or not self.fast.has_bssids:
                self.fast.set_bssids(bssid_table(base.aps))
            overlay = self.fast.overlay(now, self.fast_max_age)
            for mac in apply_overlay(data.clients, data.aps, overlay):
                self._last_connected[mac] = now

        for mac, client in data.clients.items():
            client["lastConnected"] = self._last_connected.get(mac)
            rates = self._rates.get(mac) if client["connected"] else None
            client["uploadRate"], client["downloadRate"] = rates or (None, None)

        self._detect_device_uplinks(data)
        self._apply_manual_uplinks(data)
        self._detect_events(data)
        self.changes.update(data.clients, now)
        self._readd_removed(data)
        return data

    async def async_load_removed(self) -> None:
        self.removed = set(await self._removed_store.async_load() or [])

    @callback
    def async_mark_removed(self, macs: set[str]) -> None:
        """Remember clients whose device was removed from Home Assistant."""
        if macs - self.removed:
            self.removed |= macs
            self._removed_store.async_delay_save(lambda: sorted(self.removed), 5)

    def is_stale(self, mac: str, client: dict[str, Any], now: datetime) -> bool:
        """Not connected for longer than the "remove after" option."""
        days = self.config_entry.options.get(CONF_REMOVE_AFTER_DAYS, DEFAULT_REMOVE_AFTER_DAYS)
        return is_stale(
            bool(client.get("connected")),
            parse_timestamp(client.get("lastSeen"), client.get("timeZone")),
            self._last_connected.get(mac),
            now,
            days,
        )

    def _readd_removed(self, data: GWNData) -> None:
        """A removed client connected again: forget the removal and reload to add it."""
        back = {mac for mac in self.removed if data.clients.get(mac, {}).get("connected")}
        if back:
            self.removed -= back
            self._removed_store.async_delay_save(lambda: sorted(self.removed), 5)
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    @callback
    def async_fast_update(self) -> None:
        """Publish a change from the fast sources without an API request.

        Not async_set_updated_data: that restarts the polling timer, and
        frequent fast updates would then keep the API from being polled.
        """
        if self._base is None:
            return
        self.data = self._compose(dt_util.utcnow())
        self.async_update_listeners()

    def _apply_ap_clients(self, data: GWNData, full_fetched: bool, now: datetime) -> None:
        """Add clients that only the APs' own client lists show as connected.

        GWN Manager sometimes drops a connected client from client/list while
        the AP still lists it (with the current AP and band). The AP lists are
        only fetched with the full update, so only clients missing from
        client/list right after that fetch count; a client that disconnects
        later simply drops out of client/list and is not held back.
        """
        if full_fetched:
            self._ap_only = {
                mac
                for mac in self._full.ap_clients
                if not data.clients.get(mac, {}).get("connected")
            }
        for mac in list(self._ap_only):
            client = data.clients.get(mac)
            if client and client.get("connected"):
                # Back in client/list, which has the fresher values.
                self._ap_only.discard(mac)
                continue
            ap_client = self._full.ap_clients[mac]
            merged = data.clients.setdefault(
                mac, {"clientId": mac, "timeZone": data.networks.get(ap_client["networkId"])}
            )
            merged.update(ap_client)
            merged["connected"] = True
            merged["wired"] = 2
            self._last_connected[mac] = now

    def _detect_device_uplinks(self, data: GWNData) -> None:
        """Guess which switch port each AP and the router are plugged into.

        The API has no LLDP or MAC table, so this is derived:
        - AP -> switch port: same link speed as the AP's uplink, and the number
          of devices the switch sees on the port (clientCount) closest to the
          AP's clients + the AP itself (+1 per device on its LAN port).
        - Switch -> router: the switch port on which the router's own wireless
          clients are seen (their "port" field); the router side is its only
          connected LAN port.
        """
        for ap in data.aps.values():
            ap.pop("uplink", None)
        switches = [mac for mac, ap in data.aps.items() if ap.get("ports")]
        if len(switches) != 1:
            return  # ambiguous with several switches
        switch_mac = switches[0]
        ports = {
            str(p.get("silkScreenPort")): p
            for p in data.aps[switch_mac]["ports"]
            if p.get("operStatus") == "up"
        }

        def port_number(text: str | None) -> str | None:
            return text.rsplit("/", 1)[-1] if text else None

        clients_per_ap = Counter(
            (c.get("apId") or "").upper() for c in data.clients.values() if c.get("connected")
        )
        used: set[str] = set()

        # Router uplink
        for mac, ap in data.aps.items():
            if ap.get("deviceType") != "ROUTER":
                continue
            seen = Counter(
                port_number(c.get("port"))
                for c in data.clients.values()
                if c.get("connected") and c.get("wired") != 1
                and (c.get("apId") or "").upper() == mac and c.get("port")
            )
            seen_on = [port for port, _ in seen.most_common() if port in ports]
            lan_up = [
                p.get("silkScreenPort")
                for p in ap.get("info", {}).get("portInfo") or []
                if p.get("linkStatus") == "1" and p.get("role") != "1"
            ]
            if seen_on and len(lan_up) == 1:
                ap["uplink"] = {"parent": mac, "port": lan_up[0], "ownPort": seen_on[0], "source": "auto"}
                # Stored on the switch: the switch is the child of the router.
                data.aps[switch_mac]["uplink"] = ap.pop("uplink")
                used.add(seen_on[0])

        # AP uplinks
        candidates = []
        for mac, ap in data.aps.items():
            links = (ap.get("details") or {}).get("linkSpeed") or []
            if mac == switch_mac or ap.get("deviceType") == "ROUTER" or not links:
                continue
            speed = next((l.get("value1") for l in links if l.get("status") == "connected"), None)
            lan_devices = sum(1 for l in links[1:] if l.get("status") == "connected")
            expected = clients_per_ap.get(mac, 0) + 1 + lan_devices
            candidates.append((mac, speed, expected))
        if not candidates or len(candidates) > 7:
            return
        speeds = {speed for _, speed, _ in candidates}
        free = [
            label
            for label, port in ports.items()
            if label not in used and port.get("operSpeed") in speeds
        ]

        def cost(mac_expected: int, port: str) -> int:
            return abs(int(ports[port].get("clientCount") or 0) - mac_expected)

        assignment: dict[str, str] = {}
        if len(free) >= len(candidates) and perm(len(free), len(candidates)) <= 50_000:
            best: tuple[int, tuple[str, ...]] | None = None
            for combo in permutations(free, len(candidates)):
                if any(ports[port].get("operSpeed") != speed for (_, speed, _), port in zip(candidates, combo)):
                    continue
                total = sum(cost(expected, port) for (_, _, expected), port in zip(candidates, combo))
                if best is None or total < best[0]:
                    best = (total, combo)
            if best:
                assignment = {mac: port for (mac, _, _), port in zip(candidates, best[1])}
        else:
            # Too many combinations: assign greedily, busiest AP first.
            for mac, speed, expected in sorted(candidates, key=lambda c: -c[2]):
                options = [p for p in free if p not in assignment.values() and ports[p].get("operSpeed") == speed]
                if options:
                    assignment[mac] = min(options, key=lambda p: cost(expected, p))
        for mac, port in assignment.items():
            data.aps[mac]["uplink"] = {"parent": switch_mac, "port": port, "ownPort": None, "source": "auto"}

    def _apply_manual_uplinks(self, data: GWNData) -> None:
        """Override the device a client is attached to, e.g. behind an AP's LAN port.

        GWN Manager attributes wired clients behind an AP's LAN port to the
        router, so the user can assign them in the options.
        """
        rules = parse_manual_uplinks(self.config_entry.options.get(CONF_MANUAL_UPLINKS))
        if not rules:
            return

        def find_device(ref: str) -> str | None:
            mac = normalize_mac(ref)
            if mac in data.aps:
                return mac
            for ap_mac, ap in data.aps.items():
                if (ap.get("name") or "").casefold() == ref.casefold():
                    return ap_mac
            return None

        def find_clients(ref: str) -> list[str]:
            mac = normalize_mac(ref)
            if mac in data.clients:
                return [mac]
            return [
                client_mac
                for client_mac, client in data.clients.items()
                if ref.casefold()
                in {
                    (client.get(key) or "").casefold()
                    for key in ("name", "clientName", "dhcpHostname")
                }
            ]

        for client_ref, device_ref, port in rules:
            if (device_mac := find_device(device_ref)) is None:
                continue
            if (child_mac := find_device(client_ref)) is not None:
                # A network device, e.g. "Office AP = Switch:23".
                own = (data.aps[child_mac].get("uplink") or {}).get("ownPort")
                data.aps[child_mac]["uplink"] = {
                    "parent": device_mac,
                    "port": port,
                    "ownPort": own,
                    "source": "manual",
                }
                continue
            for client_mac in find_clients(client_ref):
                client = data.clients[client_mac]
                client["apId"] = device_mac
                client["apName"] = data.aps[device_mac].get("name")
                client["manualUplink"] = True
                if port:
                    client["port"] = port

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

        for mac, ap in aps.items():
            self._update_wan(mac, ap)

    def _update_wan(self, mac: str, ap: dict[str, Any]) -> None:
        """Extract the WAN port of a router and derive its throughput."""
        wan = next(
            (
                port
                for port in ap.get("info", {}).get("portInfo") or []
                if port.get("role") == "1" or port.get("wanName")
            ),
            None,
        )
        if wan is None:
            return
        ipv4 = wan.get("ipv4Info") or {}
        ap["wan"] = {
            "name": wan.get("wanName") or wan.get("silkScreenPort"),
            "connected": wan.get("linkStatus") == "1" and ipv4.get("connectStatus") == "1",
            "wanIp": ipv4.get("ip4Address") or None,
            "connectDuration": int(wan["connectDuration"]) if wan.get("connectDuration") else None,
            "downloadRate": None,
            "uploadRate": None,
        }
        aggregate = wan.get("aggregate") or {}
        try:
            rx, tx = int(aggregate["rxBytes"]), int(aggregate["txBytes"])
        except (KeyError, TypeError, ValueError):
            return
        now = dt_util.utcnow()
        previous = self._wan_samples.get(mac)
        if previous is None or rx < previous[0] or tx < previous[1]:
            self._wan_samples[mac] = (rx, tx, now)
        elif (rx, tx) != previous[:2]:
            # The router refreshes its counters less often than we poll; only
            # compute a new rate when they changed and keep it until then.
            elapsed = (now - previous[2]).total_seconds()
            if elapsed > 0:
                self._wan_rates[mac] = ((rx - previous[0]) * 8 / elapsed, (tx - previous[1]) * 8 / elapsed)
            self._wan_samples[mac] = (rx, tx, now)
        elif (now - previous[2]).total_seconds() > 3 * THROUGHPUT_IDLE_AFTER:
            self._wan_rates[mac] = (0.0, 0.0)
        if (rates := self._wan_rates.get(mac)) is not None:
            ap["wan"]["downloadRate"], ap["wan"]["uploadRate"] = rates

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

            network_aps: dict[str, dict[str, Any]] = {}
            for ap in await self.client.get_access_points(network_id):
                if mac := (ap.get("mac") or "").upper():
                    network_aps[mac] = {**ap, "networkName": network.get("networkName")}
            if not network_aps:
                # Without devices the network has nothing to show: its SSIDs and client
                # history (e.g. a GWN.Cloud account whose devices are managed locally)
                # would only duplicate the entities of another entry.
                _LOGGER.debug("Network %s has no devices, skipping it", network_id)
                continue
            data.networks[network_id] = time_zone
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
                for ssid_entry in detail.get("ssid") or []:
                    for ssid_name, bands in ssid_entry.items():
                        for band, stations in (bands or {}).items():
                            for station in stations or []:
                                if not (client_mac := normalize_mac(station.get("clientMac"))):
                                    continue
                                data.ap_clients[client_mac] = {
                                    "apId": mac,
                                    "apName": ap.get("name"),
                                    "ssid": ssid_name,
                                    "channelClassStr": AP_CLIENT_BANDS.get(band, band),
                                    "rssi": station.get("rssi"),
                                    "networkId": network_id,
                                }
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
                    data.clients[mac] = {**client, "timeZone": time_zone, "wired": 2}
        return data
