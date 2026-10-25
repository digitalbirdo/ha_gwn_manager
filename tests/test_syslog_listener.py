"""Tests for the UDP syslog listener (real socket on an ephemeral port)."""

import asyncio
import socket

from fast.syslog import start_syslog

LINE = b"<29>Oct 25 13:14:47 GWN7665[ec74d7000001][1.0.27.20][AP X] daemon.notice: hostapd[1]: [.6]: ath3: AP-STA-CONNECTED 4e:ef:65:00:00:01"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_only_client_events_reach_callback_and_port_is_freed():
    async def run():
        port = free_port()
        got = []
        transport = await start_syslog(port, lambda ip, event: got.append((ip, event.kind)), host="127.0.0.1")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for payload in (LINE, b"<29>noise line", b"\xff\xfe\x00"):
                s.sendto(payload, ("127.0.0.1", port))
        await asyncio.sleep(0.2)
        stats = transport.get_protocol().stats
        transport.close()
        await asyncio.sleep(0.05)
        again = await start_syslog(port, lambda ip, event: None, host="127.0.0.1")
        again.close()
        return got, stats

    got, stats = asyncio.run(run())
    assert got == [("127.0.0.1", "connected")]
    assert stats == {"lines": 3, "events": 1}


def test_debug_events_option_passed_to_parser():
    async def run():
        port = free_port()
        got = []
        transport = await start_syslog(port, lambda ip, event: got.append(event.rssi), host="127.0.0.1", debug_events=True)
        line = b'<15>x GWN7665[ec74d7000001][1][AP] daemon.debug: controller: process_send_events: gseventlog send { "event_content": { "client_mac": "4eef65000001", "rssi": -50 }, "event_subtype": 1 }'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(line, ("127.0.0.1", port))
        await asyncio.sleep(0.2)
        transport.close()
        return got

    assert asyncio.run(run()) == [-50]
