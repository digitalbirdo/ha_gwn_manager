"""UDP syslog receiver for the GWN devices' client events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .syslog_parse import SyslogEvent, parse_syslog_line

EventCallback = Callable[[str, SyslogEvent], None]


class SyslogProtocol(asyncio.DatagramProtocol):
    """Parses each datagram and hands client events to the callback."""

    def __init__(self, on_event: EventCallback, debug_events: bool) -> None:
        self._on_event = on_event
        self._debug_events = debug_events
        self.stats = {"lines": 0, "events": 0}
        # Wall-clock time (time.time()) of the last datagram, any content.
        self.last_line: float | None = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.stats["lines"] += 1
        self.last_line = time.time()
        event = parse_syslog_line(data.decode("utf-8", "replace"), self._debug_events)
        if event is not None:
            self.stats["events"] += 1
            self._on_event(addr[0], event)


async def start_syslog(
    port: int,
    on_event: EventCallback,
    host: str = "0.0.0.0",
    debug_events: bool = False,
) -> asyncio.DatagramTransport:
    """Listen on UDP port; raises OSError if it can't be bound."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(on_event, debug_events), local_addr=(host, port)
    )
    return transport  # type: ignore[return-value]
