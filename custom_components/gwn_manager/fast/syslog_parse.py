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
_ROAM_RE = re.compile(rf"sta ({MAC}) 80211r (fast roaming|init assoc): ({MAC}) --> ({MAC})")
_AWAY_RE = re.compile(rf"gs_sta_roaming_away: addr=({MAC}), if=(\S+), reason_code=(\d+)")
_DISASSOC_RE = re.compile(rf"network-health-revice:({MAC}) disassociation received")
_STATUS_RE = re.compile(rf"network-health-revice:({MAC}) send (auth reply|assoc resp) status_code (\d+)")
_PINGPONG_RE = re.compile(rf"network_health ({MAC}) PINGPONG_ROAM")
_ROAM_STATS_RE = re.compile(
    rf"network_health_roam_statics ({MAC}) QUALITY_FAILED (\d+) STABILITY_FAILED (\d+) LATENCY_FAILED (\d+)"
)
_STEER_REQ_RE = re.compile(
    rf"dcs_cmn_handle_steer_req: .* for STA ({MAC}), steer type (\w+), STA type (\w+), RSSI (\d+), channel (\d+)"
)
_STEER_RESP_RE = re.compile(
    rf"dcs_send_steer_resp: .* for STA ({MAC}),? result code ([A-Z ]+?), steer type (\w+), STA type (\w+)"
)
# Cheap substring check before any regex: ~150 lines/s arrive at level debug.
_MARKERS = (
    "AP-STA-",
    "Sta is freed",
    "DHCPACK",
    "80211r",
    "gs_sta_roaming_away",
    "disassociation received",
    "status_code",
    "PINGPONG_ROAM",
    "network_health_roam_statics",
    "dcs_cmn_handle_steer_req",
    "dcs_send_steer_resp",
)


@dataclass(frozen=True)
class SyslogEvent:
    """A client event from one syslog line."""

    kind: Literal["connected", "disconnected", "dhcp", "info", "diag"]
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
    # Roaming/connection diagnostics (kind "diag"): what happened and the details.
    diag: str | None = None
    from_bssid: str | None = None
    reason_code: int | None = None
    status_code: int | None = None
    result: str | None = None
    snr: int | None = None
    detail: str | None = None


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
    if not gsevent and not any(marker in line for marker in _MARKERS):
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
    return _parse_diag(line, tag, debug_events)


def _parse_diag(line: str, tag: str | None, debug_events: bool) -> SyslogEvent | None:
    """Roaming and connection diagnostics; most of them are only sent at level debug."""
    if match := _ROAM_RE.search(line):
        return SyslogEvent(
            "diag",
            _mac(match.group(1)),
            diag="roam_fast" if match.group(2) == "fast roaming" else "roam_init",
            from_bssid=_mac(match.group(3)),
            bssid=_mac(match.group(4)),
            device_tag=tag,
        )
    if match := _AWAY_RE.search(line):
        return SyslogEvent(
            "diag",
            _mac(match.group(1)),
            diag="roam_away",
            ifname=match.group(2),
            reason_code=int(match.group(3)),
            device_tag=tag,
        )
    if not debug_events:
        return None
    event = None
    if match := _DISASSOC_RE.search(line):
        event = ("disassoc", match.group(1), {})
    elif match := _STATUS_RE.search(line):
        if int(match.group(3)):
            diag = "auth_fail" if match.group(2) == "auth reply" else "assoc_fail"
            event = (diag, match.group(1), {"status_code": int(match.group(3))})
    elif match := _PINGPONG_RE.search(line):
        event = ("pingpong", match.group(1), {})
    elif match := _ROAM_STATS_RE.search(line):
        quality, stability, latency = (int(match.group(i)) for i in (2, 3, 4))
        if quality or stability or latency:
            detail = f"quality={quality} stability={stability} latency={latency}"
            event = ("roam_stats", match.group(1), {"detail": detail})
    elif match := _STEER_REQ_RE.search(line):
        extra = {"detail": f"{match.group(2)} {match.group(3)}", "snr": int(match.group(4)), "channel": int(match.group(5))}
        event = ("steer_req", match.group(1), extra)
    elif match := _STEER_RESP_RE.search(line):
        extra = {"result": match.group(2), "detail": f"{match.group(3)} {match.group(4)}"}
        event = ("steer_resp", match.group(1), extra)
    if event is None:
        return None
    diag, mac, extra = event
    return SyslogEvent("diag", _mac(mac), diag=diag, device_tag=tag, debug=True, **extra)


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
