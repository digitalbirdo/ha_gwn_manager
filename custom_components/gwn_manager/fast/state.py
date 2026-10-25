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
            new = {
                **(entry if entry and entry.get("apId") == ap_mac else {}),
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
