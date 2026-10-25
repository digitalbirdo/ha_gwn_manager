"""What the fast sources (syslog, SNMP) currently say about each client.

The coordinator lays this over the API data. Entries older than a given age
are left out, so the API takes over again when the fast sources go quiet.
The overlay uses the keys of the API client records (apId, rssi, txRate,
channelClassStr, …) so entities and cards need no changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .snmp_parse import SnmpClient
from .syslog_parse import SyslogEvent

# Keys compared to tell whether an SNMP poll changed anything.
_VALUE_KEYS = ("connected", "apId", "bssid", "rssi", "snr", "txRate", "channelClassStr")


def band_from_channel(channel: int | None) -> str | None:
    """Band of a Wi-Fi channel number, or None where it's ambiguous.

    2.4 GHz uses 1-14, 5 GHz 36-144 in steps of 4 and 149-165, 6 GHz 1-233 in
    steps of 4 starting at 1 – so 1, 5, 9, 13 and 149-165 can't be told apart.
    """
    if channel is None:
        return None
    if 1 <= channel <= 14:
        return None if channel % 4 == 1 else "2.4G"
    if 36 <= channel <= 144 and channel % 4 == 0:
        return "5G"
    if 17 <= channel <= 233 and channel % 4 == 1 and not 149 <= channel <= 165:
        return "6G"
    return None


def learn_bands(
    fast: FastState, base: dict[str, dict[str, Any]], overlay: dict[str, dict[str, Any]]
) -> None:
    """Learn BSSID -> band from clients the live API list shows with the same AP.

    Skips records from the APs' own lists (up to one full update old) and
    records whose channel contradicts their band.
    """
    for mac, entry in overlay.items():
        client = base.get(mac)
        if (
            not client
            or not client.get("connected")
            or client.get("fromApList")
            or not entry.get("bssid")
            or not (band := client.get("channelClassStr"))
            or (client.get("apId") or "").upper() != entry.get("apId")
        ):
            continue
        if (from_channel := band_from_channel(client.get("channel"))) and from_channel != band:
            continue
        fast.learn_band(entry["bssid"], band)


class FastState:
    """Client state from syslog and SNMP, keyed by upper-case MAC."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        # Time of the last syslog connect/disconnect per client.
        self._syslog_at: dict[str, datetime] = {}
        self._bands: dict[str, str] = {}

    def learn_band(self, bssid: str, band: str) -> None:
        """Remember the band of a BSSID (from a client the API lists with both)."""
        self._bands[bssid.upper()] = band

    def apply_syslog(self, ap_mac: str, event: SyslogEvent, now: datetime) -> bool:
        """Apply a connect/disconnect from the device ap_mac; True if anything changed."""
        if event.kind not in ("connected", "disconnected"):
            return False
        mac = event.client_mac
        entry = self._entries.get(mac)
        if event.kind == "disconnected":
            if entry is None or not entry["connected"] or entry.get("apId") != ap_mac:
                # Already gone, or the late goodbye of the AP the client roamed away from.
                return False
            self._entries[mac] = {**entry, "connected": False, "source": "syslog", "at": now}
            self._syslog_at[mac] = now
            return True

        new = {"connected": True, "apId": ap_mac, "source": "syslog", "at": now}
        if entry and entry.get("apId") == ap_mac:
            new = {**entry, **new}
        if event.bssid:
            new["bssid"] = event.bssid
        if event.debug:
            for key, value in (("rssi", event.rssi), ("txRate", event.rate), ("channel", event.channel)):
                if value is not None:
                    new[key] = value
            if band := band_from_channel(event.channel):
                new["channelClassStr"] = band
        changed = entry is None or not entry["connected"] or entry.get("apId") != ap_mac
        self._entries[mac] = new
        self._syslog_at[mac] = now
        return changed

    def apply_snmp(
        self, ap_mac: str, clients: dict[str, SnmpClient], now: datetime, started: datetime
    ) -> bool:
        """Apply one SNMP poll of ap_mac that began at started; True if anything changed."""
        changed = False
        for mac, client in clients.items():
            last_syslog = self._syslog_at.get(mac)
            entry = self._entries.get(mac)
            if (
                last_syslog is not None
                and last_syslog > started
                and entry is not None
                and entry.get("apId") != ap_mac
            ):
                # Syslog saw a newer connect elsewhere while this poll ran.
                continue
            carried = entry if entry and entry.get("apId") == ap_mac else {}
            if carried.get("bssid") and carried["bssid"] != client.bssid:
                # Another radio of the same AP: channel and band from before are stale.
                carried = {k: v for k, v in carried.items() if k not in ("channel", "channelClassStr")}
            new = {
                **carried,
                "connected": True,
                "apId": ap_mac,
                "bssid": client.bssid,
                "rssi": client.rssi,
                "snr": client.snr,
                "txRate": client.rate,
                "source": "snmp",
                "at": now,
            }
            band = client.band or self._bands.get(client.bssid or "")
            if band:
                new["channelClassStr"] = band
            if entry is None or any(entry.get(k) != new.get(k) for k in _VALUE_KEYS):
                changed = True
            self._entries[mac] = new
        return changed

    def overlay(self, now: datetime, max_age: timedelta) -> dict[str, dict[str, Any]]:
        """Entries not older than max_age, without the bookkeeping keys."""
        return {
            mac: {k: v for k, v in entry.items() if k != "at" and v is not None}
            for mac, entry in self._entries.items()
            if now - entry["at"] <= max_age
        }


def apply_overlay(
    clients: dict[str, dict[str, Any]],
    aps: dict[str, dict[str, Any]],
    overlay: dict[str, dict[str, Any]],
) -> set[str]:
    """Lay the fast-source overlay over the API clients (in place).

    Returns the MACs the overlay reports as connected. A disconnect only
    applies to clients the API knows; a connect may add a client the API
    hasn't listed yet.
    """
    connected: set[str] = set()
    for mac, entry in overlay.items():
        values = {k: v for k, v in entry.items() if k != "source"}
        values["fastSource"] = entry.get("source")
        if not entry["connected"]:
            if mac in clients:
                clients[mac]["connected"] = False
                clients[mac]["fastSource"] = values["fastSource"]
            continue
        client = clients.setdefault(mac, {"clientId": mac})
        client.update(values)
        client["apName"] = aps.get(entry.get("apId") or "", {}).get("name")
        client["wired"] = 2
        connected.add(mac)
    return connected
