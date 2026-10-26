"""Signal samples and connection events per client, for the detail panel.

Kept in memory only (bounded); pure module without Home Assistant imports.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any

_SAMPLE_KEYS = ("rssi", "snr", "rate", "ap", "band")


class ClientHistory:
    """Bounded per-client history of samples and events."""

    def __init__(
        self,
        max_samples: int = 400,
        max_events: int = 100,
        gap: timedelta = timedelta(seconds=15),
    ) -> None:
        self._max_samples = max_samples
        self._max_events = max_events
        self._gap = gap
        self._samples: dict[str, deque[dict[str, Any]]] = {}
        self._events: dict[str, deque[dict[str, Any]]] = {}
        self._state: dict[str, tuple[bool, str | None, str | None, str | None]] = {}

    def record(self, clients: dict[str, dict[str, Any]], now: datetime) -> None:
        """Note what changed since the last call (call after every update)."""
        for mac, client in clients.items():
            connected = bool(client.get("connected"))
            ap = (client.get("apId") or "").upper() or None
            band = client.get("channelClassStr")
            name = client.get("apName")
            previous = self._state.get(mac)
            self._state[mac] = (connected, ap, band, name)
            if previous is not None:
                self._note_event(mac, previous, connected, ap, band, name, client, now)
            if connected:
                self._maybe_sample(mac, client, now)

    def add_sample(self, mac: str, client: dict[str, Any], now: datetime, source: str) -> None:
        """Always add a sample (live measurement)."""
        self._append(self._samples, mac, self._sample(client, now, source), self._max_samples)

    def get(self, mac: str) -> dict[str, list[dict[str, Any]]]:
        return {
            "samples": list(self._samples.get(mac, ())),
            "events": list(self._events.get(mac, ())),
        }

    def dump(self, max_samples: int = 500) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Samples (the latest max_samples per client) and events, for saving."""
        return {
            "samples": {mac: list(items)[-max_samples:] for mac, items in self._samples.items() if items},
            "events": {mac: list(items) for mac, items in self._events.items() if items},
        }

    def load(self, data: Any) -> None:
        """Take over saved data; anything that isn't in the expected shape is ignored."""
        if not isinstance(data, dict):
            return
        for key, store, limit in (
            ("samples", self._samples, self._max_samples),
            ("events", self._events, self._max_events),
        ):
            saved = data.get(key)
            if not isinstance(saved, dict):
                continue
            for mac, items in saved.items():
                if isinstance(items, list):
                    store[mac] = deque((i for i in items if isinstance(i, dict) and "t" in i), maxlen=limit)

    def _note_event(
        self,
        mac: str,
        previous: tuple[bool, str | None, str | None, str | None],
        connected: bool,
        ap: str | None,
        band: str | None,
        name: str | None,
        client: dict[str, Any],
        now: datetime,
    ) -> None:
        was_on, was_ap, was_band, was_name = previous
        event: dict[str, Any] | None = None
        if connected and not was_on:
            event = {"kind": "connected"}
        elif was_on and not connected:
            event = {"kind": "disconnected"}
            ap, band, name = ap or was_ap, band or was_band, name or was_name
        elif connected and ap != was_ap:
            event = {"kind": "roamed", "from_ap": was_ap, "from_ap_name": was_name, "from_band": was_band}
        elif connected and band and was_band and band != was_band:
            event = {"kind": "band", "from_band": was_band}
        if event is None:
            return
        event.update(
            {
                "t": now.isoformat(),
                "ap": ap,
                "ap_name": name,
                "band": band,
                "rssi": client.get("rssi"),
                "source": client.get("fastSource") or "api",
            }
        )
        self._append(self._events, mac, event, self._max_events)

    def _maybe_sample(self, mac: str, client: dict[str, Any], now: datetime) -> None:
        sample = self._sample(client, now, client.get("fastSource") or "api")
        last = self._samples.get(mac, deque())[-1] if self._samples.get(mac) else None
        if (
            last is not None
            and all(last[k] == sample[k] for k in _SAMPLE_KEYS)
            and now - datetime.fromisoformat(last["t"]) < self._gap
        ):
            return
        self._append(self._samples, mac, sample, self._max_samples)

    @staticmethod
    def _sample(client: dict[str, Any], now: datetime, source: str) -> dict[str, Any]:
        return {
            "t": now.isoformat(),
            "rssi": client.get("rssi"),
            "snr": client.get("snr"),
            "rate": client.get("txRate"),
            "ap": (client.get("apId") or "").upper() or None,
            "ap_name": client.get("apName"),
            "band": client.get("channelClassStr"),
            "source": source,
        }

    @staticmethod
    def _append(store: dict[str, deque], mac: str, item: dict[str, Any], limit: int) -> None:
        store.setdefault(mac, deque(maxlen=limit)).append(item)
