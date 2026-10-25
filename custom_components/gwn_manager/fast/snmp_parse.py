"""Assemble the SNMP client tables of GWN devices into client records.

Pure functions without Home Assistant imports. The APs (GWN76xx) and the
routers (GWN70xx) have different tables; both are read column by column, so
a client that leaves between two column walks shifts the rows. Rows without
a MAC or BSSID are therefore dropped instead of guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Noise floor assumed to turn the APs' SNR (dB) into an RSSI (dBm).
NOISE_FLOOR = -95


class MibVariant(StrEnum):
    """Which client table a device has."""

    AP = "ap"
    ROUTER = "router"


TABLES = {
    MibVariant.AP: "1.3.6.1.4.1.42397.1.1.3.3.1",
    MibVariant.ROUTER: "1.3.6.1.4.1.42397.1.2.1.3.1",
}
# Only the columns the API doesn't refresh fast enough: every column is one
# walk of ~0.25 s per client (IP column 3/4, SSID 5/7, hostname 9/11 exist too).
COLUMNS: dict[MibVariant, dict[str, int]] = {
    MibVariant.AP: {"mac": 2, "bssid": 4, "signal": 6, "rate": 11},
    MibVariant.ROUTER: {"mac": 2, "bssid": 3, "band": 6, "signal": 8},
}
ROUTER_BANDS = {"2.4GHZ": "2.4G", "5GHZ": "5G", "6GHZ": "6G"}


@dataclass
class SnmpClient:
    """One Wi-Fi client as a device's SNMP table lists it."""

    mac: str
    bssid: str | None
    ip: str | None
    ssid: str | None
    rssi: int | None
    snr: int | None
    rate: int | None
    band: str | None
    hostname: str | None


def normalize_mac(value: str | None) -> str | None:
    """Upper-case MAC with colons, from either notation; None if invalid."""
    raw = (value or "").replace(":", "").replace("-", "").strip().upper()
    if len(raw) != 12 or any(ch not in "0123456789ABCDEF" for ch in raw):
        return None
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def _int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def build_clients(variant: MibVariant, columns: dict[str, dict[str, str]]) -> dict[str, SnmpClient]:
    """Turn column name -> {row index -> value} into clients keyed by MAC."""

    def cell(name: str, row: str) -> str | None:
        return columns.get(name, {}).get(row) or None

    clients: dict[str, SnmpClient] = {}
    for row, raw_mac in columns.get("mac", {}).items():
        mac = normalize_mac(raw_mac)
        bssid = normalize_mac(cell("bssid", row))
        if not mac or not bssid:
            continue
        signal = _int(cell("signal", row))
        if variant is MibVariant.AP:
            snr, rssi = signal, None if signal is None else signal + NOISE_FLOOR
            band = None
        else:
            snr, rssi = None, signal
            band = ROUTER_BANDS.get((cell("band", row) or "").upper())
        clients[mac] = SnmpClient(
            mac=mac,
            bssid=bssid,
            ip=cell("ip", row),
            ssid=cell("ssid", row),
            rssi=rssi,
            snr=snr,
            rate=_int(cell("rate", row)),
            band=band,
            hostname=cell("hostname", row),
        )
    return clients
