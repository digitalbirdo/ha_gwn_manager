"""Which clients are old enough to be removed from Home Assistant.

Pure module without Home Assistant imports.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def is_stale(
    connected: bool,
    last_seen: datetime | None,
    last_connected: datetime | None,
    now: datetime,
    days: int,
) -> bool:
    """True if the client hasn't been connected for more than `days` (0 = never)."""
    if not days or connected:
        return False
    seen = [t for t in (last_seen, last_connected) if t is not None]
    if not seen:
        return False
    return now - max(seen) > timedelta(days=days)
