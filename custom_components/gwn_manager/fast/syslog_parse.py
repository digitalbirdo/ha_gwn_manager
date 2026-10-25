"""Parse the few syslog lines of GWN devices that tell about clients.

Pure functions without Home Assistant imports, so they can be tested alone.
The devices send ~40 lines/s at level notice (~150/s at debug); everything
that isn't a client event is dropped by a cheap substring check first.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Literal

MAC = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
_TAG_RE = re.compile(r"GWN\w+\[([0-9A-Fa-f]{12})\]")
_STA_RE = re.compile(rf"(\S+): AP-STA-(CONNECTED|DISCONNECTED) ({MAC})")
_FREED_RE = re.compile(rf"Sta is freed: sta=({MAC}), bssid=({MAC}), if=([^,\s]+)")
_DHCP_RE = re.compile(rf"DHCPACK\([^)]*\) (\S+) ({MAC})(?: (\S+))?")
_GSEVENT = "gseventlog send "


@dataclass(frozen=True)
class SyslogEvent:
    """A client event from one syslog line."""

    kind: Literal["connected", "disconnected", "dhcp", "info"]
    client_mac: str
    ifname: str | None = None
    bssid: str | None = None
    ip: str | None = None
    hostname: str | None = None
    device_tag: str | None = None
    ap_mac: str | None = None
    rssi: int | None = None
    rate: int | None = None
    channel: int | None = None
    sta_mode: str | None = None
    os: str | None = None
    debug: bool = False


def _mac(value: str | None) -> str | None:
    """Upper-case MAC with colons, from either notation."""
    if not value:
        return None
    raw = value.replace(":", "").replace("-", "").upper()
    if len(raw) != 12:
        return None
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_syslog_line(line: str, debug_events: bool = False) -> SyslogEvent | None:
    """Return the client event in a syslog line, or None for anything else."""
    gsevent = debug_events and _GSEVENT in line
    if not gsevent and "AP-STA-" not in line and "Sta is freed" not in line and "DHCPACK" not in line:
        return None
    tag_match = _TAG_RE.search(line)
    tag = tag_match.group(1).upper() if tag_match else None

    if gsevent:
        return _parse_gsevent(line, tag)
    if match := _STA_RE.search(line):
        kind = "connected" if match.group(2) == "CONNECTED" else "disconnected"
        return SyslogEvent(kind, _mac(match.group(3)), ifname=match.group(1), device_tag=tag)
    if match := _FREED_RE.search(line):
        return SyslogEvent(
            "disconnected",
            _mac(match.group(1)),
            bssid=_mac(match.group(2)),
            ifname=match.group(3),
            device_tag=tag,
        )
    if match := _DHCP_RE.search(line):
        return SyslogEvent(
            "dhcp", _mac(match.group(2)), ip=match.group(1), hostname=match.group(3), device_tag=tag
        )
    return None


def _parse_gsevent(line: str, tag: str | None) -> SyslogEvent | None:
    """The JSON event the APs send to GWN Manager (only at syslog level debug)."""
    try:
        event = json.loads(line.split(_GSEVENT, 1)[1])
        content = event["event_content"]
        client = _mac(content.get("client_mac"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if not client:
        return None
    subtype = event.get("event_subtype")
    if subtype is None:
        if "hostname" not in content and "os" not in content:
            return None
        return SyslogEvent(
            "info",
            client,
            ip=content.get("ipv4") or None,
            hostname=content.get("hostname") or None,
            os=content.get("os") or None,
            device_tag=tag,
            debug=True,
        )
    if subtype not in (0, 1):
        return None
    return SyslogEvent(
        "connected" if subtype == 1 else "disconnected",
        client,
        device_tag=tag,
        ap_mac=_mac(content.get("ap_mac")),
        rssi=_int(content.get("rssi")),
        rate=_int(content.get("tx_rate")),
        channel=_int(content.get("channel")),
        sta_mode=content.get("new_sta_mode"),
        debug=True,
    )
