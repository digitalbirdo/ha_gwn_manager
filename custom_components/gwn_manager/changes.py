"""Remember the last meaningful change of each client, for the cards' flash.

Meaningful: connected, roamed, disconnected, or the signal moved to another
colour step of the cards. Pure module without Home Assistant imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Same steps as rssiColor() in the cards: >= -60, >= -70, >= -80, below.
SIGNAL_STEPS = (-80, -70, -60)


def signal_step(rssi: int | None) -> int | None:
    """Colour step of an RSSI: 0 (worst) to 3 (best), None if unknown."""
    if rssi is None:
        return None
    return sum(rssi >= limit for limit in SIGNAL_STEPS)


class ChangeTracker:
    """Compares consecutive client snapshots."""

    def __init__(self) -> None:
        self._previous: dict[str, tuple[bool, str | None, int | None]] | None = None
        # Client MAC -> (kind, time of the change)
        self.changes: dict[str, tuple[str, datetime]] = {}

    def update(self, clients: dict[str, dict[str, Any]], now: datetime) -> None:
        current = {
            mac: (
                bool(client.get("connected")),
                (client.get("apId") or "").upper() or None,
                signal_step(client.get("rssi")) if client.get("connected") else None,
            )
            for mac, client in clients.items()
        }
        previous, self._previous = self._previous, current
        if previous is None:
            return
        for mac in previous.keys() | current.keys():
            was_on, was_ap, was_step = previous.get(mac, (False, None, None))
            is_on, is_ap, is_step = current.get(mac, (False, None, None))
            if is_on and not was_on:
                kind = "connected"
            elif was_on and not is_on:
                kind = "disconnected"
            elif is_on and is_ap != was_ap:
                kind = "roamed"
            elif is_on and is_step is not None and was_step is not None and is_step != was_step:
                kind = "signal"
            else:
                continue
            self.changes[mac] = (kind, now)
