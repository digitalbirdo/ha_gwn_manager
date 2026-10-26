"""UDP syslog receiver for the GWN devices' client events."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import ipaddress
import socket
import time

from .syslog_parse import SyslogEvent, parse_syslog_line

EventCallback = Callable[[str, SyslogEvent], None]
Target = tuple[str, int]


def parse_forward_targets(text: str | None, own_port: int | None = None) -> list[Target]:
    """Parse "ip:port" targets separated by commas, spaces or new lines.

    Only IPv4 addresses (no host names: resolving would block). Raises ValueError for
    anything invalid, and for a loopback or wildcard target on the receiver's own port,
    which would forward every message to itself in an endless loop.
    """
    targets: list[Target] = []
    for item in (text or "").replace(",", " ").split():
        host, sep, port_text = item.rpartition(":")
        if not sep or not host or not port_text.isdigit():
            raise ValueError(f"expected ip:port, got {item!r}")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range in {item!r}")
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError as err:
            raise ValueError(f"not an IPv4 address: {host!r}") from err
        if own_port is not None and port == own_port and (address.is_loopback or address.is_unspecified):
            raise ValueError(f"{item!r} is this receiver itself")
        if (target := (str(address), port)) not in targets:
            targets.append(target)
    return targets


class SyslogProtocol(asyncio.DatagramProtocol):
    """Parses each datagram and hands client events to the callback."""

    def __init__(
        self, on_event: EventCallback, debug_events: bool, forward: Iterable[Target] = ()
    ) -> None:
        self._on_event = on_event
        self._debug_events = debug_events
        self._forward = list(forward)
        self._out: socket.socket | None = None
        if self._forward:
            self._out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._out.setblocking(False)
        self.stats = {"lines": 0, "events": 0, "forwarded": 0, "forward_errors": 0}
        # Wall-clock time (time.time()) of the last datagram, any content.
        self.last_line: float | None = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.stats["lines"] += 1
        self.last_line = time.time()
        # Unchanged and first, so others get the complete stream whatever we make of it.
        if self._out is not None:
            for target in self._forward:
                try:
                    self._out.sendto(data, target)
                    self.stats["forwarded"] += 1
                except OSError:
                    self.stats["forward_errors"] += 1
        event = parse_syslog_line(data.decode("utf-8", "replace"), self._debug_events)
        if event is not None:
            self.stats["events"] += 1
            self._on_event(addr[0], event)


    def connection_lost(self, exc: Exception | None) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None


async def start_syslog(
    port: int,
    on_event: EventCallback,
    host: str = "0.0.0.0",
    debug_events: bool = False,
    forward: Iterable[Target] = (),
) -> asyncio.DatagramTransport:
    """Listen on UDP port and forward every datagram unchanged to the targets.

    Raises OSError if the port can't be bound.
    """
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(on_event, debug_events, forward), local_addr=(host, port)
    )
    return transport  # type: ignore[return-value]
