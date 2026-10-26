"""Roaming and connection diagnosis of one client, from the syslog events.

Pure module without Home Assistant imports. DiagnosisLog keeps the events of
each client (bounded); analyze() turns them, together with the signal samples
of ClientHistory, into statistics and findings. Findings carry an id and
parameters only; the card words them.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Any

# The same event from two log lines (e.g. AP-STA-DISCONNECTED and "Sta is freed").
_DUPLICATE_WINDOW = timedelta(seconds=2)
# Steering requests come in storms; these are counted in one entry instead.
_COUNTED_TYPES = ("steer_req", "steer_resp")
_COUNT_WINDOW = timedelta(seconds=60)
# A client that doesn't come back within this time after a disconnect has dropped out.
RECONNECT_WITHIN = timedelta(seconds=30)
# Back to the previous AP within this time counts as ping-pong.
PINGPONG_WITHIN = timedelta(seconds=60)
# Signal at or below which a client is "sticky" if it doesn't move.
WEAK_RSSI = -75
# A disconnect at most this long after the last note of a cause is attributed to it.
_CAUSE_WINDOW = timedelta(seconds=5)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


class DiagnosisLog:
    """Bounded per-client list of connection and roaming events."""

    def __init__(self, max_events: int = 1500, max_age: timedelta = timedelta(days=7)) -> None:
        self._max_events = max_events
        self._max_age = max_age
        self._events: dict[str, deque[dict[str, Any]]] = {}

    def add(self, mac: str, event: dict[str, Any], now: datetime) -> None:
        """Add an event (keys: type, ap, ...); t is set to now."""
        event = {k: v for k, v in event.items() if v is not None} | {"t": now.isoformat()}
        events = self._events.setdefault(mac, deque(maxlen=self._max_events))
        while events and now - _time(events[0]["t"]) > self._max_age:
            events.popleft()
        kind = event["type"]
        for old in reversed(events):
            age = now - _time(old.get("t_last") or old["t"])
            if age > (_COUNT_WINDOW if kind in _COUNTED_TYPES else _DUPLICATE_WINDOW):
                break
            if old["type"] != kind or old.get("ap") != event.get("ap"):
                continue
            if kind in _COUNTED_TYPES:
                if old.get("result") == event.get("result") and old.get("detail") == event.get("detail"):
                    old["count"] = old.get("count", 1) + 1
                    old["t_last"] = event["t"]
                    return
            elif old.get("ifname") in (None, event.get("ifname")) or event.get("ifname") is None:
                # Same event again: keep the first, add what the second knows more.
                for key, value in event.items():
                    old.setdefault(key, value)
                return
        events.append(event)

    def get(self, mac: str, since: datetime | None = None) -> list[dict[str, Any]]:
        events = self._events.get(mac, ())
        if since is None:
            return [dict(e) for e in events]
        return [dict(e) for e in events if _time(e.get("t_last") or e["t"]) >= since]

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        return {mac: [dict(e) for e in events] for mac, events in self._events.items() if events}

    def load(self, data: Any, now: datetime) -> None:
        """Take over saved events (older than max_age and broken entries are left out)."""
        if not isinstance(data, dict):
            return
        for mac, events in data.items():
            if not isinstance(events, list):
                continue
            kept: deque[dict[str, Any]] = deque(maxlen=self._max_events)
            for event in events:
                try:
                    if isinstance(event, dict) and "type" in event and now - _time(event["t"]) <= self._max_age:
                        kept.append(event)
                except (KeyError, TypeError, ValueError):
                    continue
            if kept:
                self._events[mac] = kept

    def forget(self, mac: str) -> None:
        self._events.pop(mac, None)


def bssid_map(aps: dict[str, dict[str, Any]], table_for) -> dict[str, dict[str, Any]]:
    """BSSID -> AP MAC, band, channel and SSID; table_for(aps) is fast.state.bssid_table."""
    result: dict[str, dict[str, Any]] = {}
    for mac, ap in aps.items():
        for bssid, entry in table_for({mac: ap}).items():
            result[bssid] = {**entry, "ap": mac}
    return result


def _band_near(samples: list[dict[str, Any]], at: datetime, ap: str | None) -> str | None:
    best: tuple[timedelta, str] | None = None
    for sample in samples:
        if not sample.get("band") or (ap and sample.get("ap") != ap):
            continue
        distance = abs(_time(sample["t"]) - at)
        if distance <= timedelta(seconds=30) and (best is None or distance < best[0]):
            best = (distance, sample["band"])
    return best[1] if best else None


def annotate(
    events: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    bssids: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add band (and the AP and band moved away from) to the events, where known."""
    for event in events:
        if info := bssids.get(event.get("bssid") or ""):
            event.setdefault("band", info.get("channelClassStr"))
        if info := bssids.get(event.get("from_bssid") or ""):
            event["from_ap"] = info["ap"]
            event["from_band"] = info.get("channelClassStr")
        if "band" not in event and event["type"] in ("connected", "disconnected"):
            if band := _band_near(samples, _time(event["t"]), event.get("ap")):
                event["band"] = band
    return events


def _moves(events: list[dict[str, Any]], bssids: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The client's attachments in order: each connect (or state-level roam) with its kind."""
    moves: list[dict[str, Any]] = []
    roams = [e for e in events if e["type"] in ("roam_fast", "roam_init")]
    last_ap: str | None = None
    last_ifname: str | None = None
    for event in events:
        if event["type"] == "connected":
            ap, ifname = event.get("ap"), event.get("ifname")
        elif event["type"] == "roam_state":
            ap, ifname = event.get("ap"), None
        else:
            continue
        at = _time(event["t"])
        how = "connect"
        for roam in roams:
            if abs(_time(roam["t"]) - at) <= timedelta(seconds=3) and roam.get("ap") in (ap, None):
                if roam["type"] == "roam_fast":
                    how = "fast"
                elif roam.get("from_bssid") not in (None, "00:00:00:00:00:00"):
                    how = "init"
                break
        if event["type"] == "roam_state":
            how = "roam"
        kind = "first"
        if last_ap is not None:
            kind = "roam" if ap != last_ap else ("band" if ifname and last_ifname and ifname != last_ifname else "same")
        moves.append(
            {"t": event["t"], "ap": ap, "ifname": ifname, "how": how, "kind": kind, "from_ap": last_ap,
             "bssid": event.get("bssid")}
        )
        last_ap, last_ifname = ap, ifname or last_ifname
    return moves


def analyze(
    events: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    now: datetime,
    *,
    debug_events: bool = False,
    blind_aps: set[str] | frozenset[str] = frozenset(),
    current_ap: str | None = None,
) -> dict[str, Any]:
    """Statistics and findings for the events and samples of one window.

    blind_aps: devices that send no usable syslog (e.g. the router), whose
    roams are only seen through SNMP/API samples.
    """
    events = sorted(events, key=lambda e: e["t"])
    counts = Counter(e["type"] for e in events)
    moves = _moves(events, {})
    stats: dict[str, Any] = {
        "connects": counts["connected"],
        "disconnects": counts["disconnected"],
        "roams": sum(1 for m in moves if m["kind"] == "roam"),
        "roams_fast": sum(1 for m in moves if m["kind"] == "roam" and m["how"] == "fast"),
        "roams_init": sum(1 for m in moves if m["kind"] == "roam" and m["how"] == "init"),
        "band_changes": sum(1 for m in moves if m["kind"] == "band"),
        "pingpong": 0,
        "auth_failures": counts["auth_fail"] + counts["assoc_fail"],
        "steer_requests": sum(e.get("count", 1) for e in events if e["type"] == "steer_req"),
        "steer_no_candidate": sum(e.get("count", 1) for e in events if e["type"] == "steer_resp" and e.get("result") == "NO CANDIDATE"),
        "steer_success": sum(e.get("count", 1) for e in events if e["type"] == "steer_resp" and e.get("result") == "SUCCESS"),
        "client_disassoc": counts["disassoc"],
        "reasons": dict(Counter(str(e["reason_code"]) for e in events if "reason_code" in e)),
        "per_ap": dict(Counter(m["ap"] for m in moves if m["ap"])),
    }

    # Ping-pong: A -> B -> A within a minute.
    roam_moves = [m for m in moves if m["kind"] == "roam"]
    pingpong = 0
    for previous, move in zip(roam_moves, roam_moves[1:]):
        if move["ap"] == previous["from_ap"] and _time(move["t"]) - _time(previous["t"]) <= PINGPONG_WITHIN:
            pingpong += 1
    stats["pingpong"] = max(pingpong, counts["pingpong"])

    # Drop-outs: a disconnect without a reconnect soon after.
    attach_times = [_time(e["t"]) for e in events if e["type"] in ("connected", "roam_state")]
    drops: list[dict[str, Any]] = []
    for event in events:
        if event["type"] != "disconnected":
            continue
        at = _time(event["t"])
        if any(at < t <= at + RECONNECT_WITHIN for t in attach_times):
            continue
        if at + RECONNECT_WITHIN > now:
            continue  # not known yet
        cause, detail = "unknown", None
        for other in events:
            if other["type"] in ("disassoc", "roam_away") and timedelta(0) <= at - _time(other["t"]) <= _CAUSE_WINDOW:
                cause, detail = ("client" if other["type"] == "disassoc" else "roamed"), other.get("reason_code")
        if cause == "unknown":
            before = [s for s in samples if s.get("rssi") is not None and _time(s["t"]) <= at]
            if before and at - _time(before[-1]["t"]) <= timedelta(minutes=2) and before[-1]["rssi"] <= WEAK_RSSI:
                cause, detail = "weak_signal", before[-1]["rssi"]
        drops.append({"t": event["t"], "ap": event.get("ap"), "cause": cause, "detail": detail})
    stats["drops"] = len(drops)
    stats["drop_causes"] = dict(Counter(d["cause"] for d in drops))

    # Signal.
    rssis = [s["rssi"] for s in samples if s.get("rssi") is not None]
    stats["rssi_min"] = min(rssis) if rssis else None
    stats["rssi_avg"] = round(sum(rssis) / len(rssis)) if rssis else None
    weak = sum(1 for r in rssis if r <= WEAK_RSSI)
    stats["weak_share"] = round(weak / len(rssis), 2) if rssis else None

    findings = _findings(stats, rssis, weak, debug_events, blind_aps, current_ap, drops)
    return {"stats": stats, "moves": moves, "drops": drops, "findings": findings}


def _findings(
    stats: dict[str, Any],
    rssis: list[int],
    weak: int,
    debug_events: bool,
    blind_aps: set[str] | frozenset[str],
    current_ap: str | None,
    drops: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def add(id_: str, confidence: str, **params: Any) -> None:
        findings.append({"id": id_, "confidence": confidence, "params": params})

    sticky_signal = len(rssis) >= 5 and weak / len(rssis) >= 0.3
    # Steering that finds no target only points to a sticky client if the signal is poor too.
    weakest = stats["rssi_min"] is not None and stats["rssi_min"] <= -70
    if sticky_signal or (stats["steer_no_candidate"] >= 5 and weakest):
        confidence = "high" if sticky_signal and stats["steer_no_candidate"] >= 5 else "medium"
        add("sticky", confidence, rssi_min=stats["rssi_min"], weak_share=stats["weak_share"],
            no_candidate=stats["steer_no_candidate"])
    if stats["steer_no_candidate"] >= 10 and stats["steer_success"] == 0:
        add("steer_no_target", "high" if sticky_signal else "medium", no_candidate=stats["steer_no_candidate"])
    if stats["roams_init"] >= 3 and stats["roams_fast"] == 0:
        add("no_fast_roaming", "medium", full=stats["roams_init"])
    elif stats["roams_init"] >= 3 and stats["roams_init"] > stats["roams_fast"]:
        add("no_fast_roaming", "low", full=stats["roams_init"], fast=stats["roams_fast"])
    if stats["pingpong"] >= 2:
        add("pingpong", "high" if stats["pingpong"] >= 4 else "medium", count=stats["pingpong"])
    if stats["band_changes"] >= 6:
        add("band_flapping", "medium", count=stats["band_changes"])
    if stats["auth_failures"]:
        add("auth_failures", "medium", count=stats["auth_failures"])
    causes = stats["drop_causes"]
    if causes.get("weak_signal", 0) >= 2:
        add("drops_weak_signal", "high", count=causes["weak_signal"])
    if causes.get("client", 0) >= 3:
        add("drops_client", "low", count=causes["client"])
    if stats["drops"] >= 3 and causes.get("unknown", 0) >= 3:
        add("drops_unknown", "low", count=causes["unknown"])
    if current_ap and current_ap in blind_aps:
        add("router_blind", "low")
    if not debug_events and not findings:
        add("need_debug", "low")
    return findings
