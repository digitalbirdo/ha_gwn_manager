"""Tests for forwarding received syslog datagrams (made-up addresses)."""

import asyncio
import socket

import pytest

from fast.syslog import parse_forward_targets, start_syslog


def test_parse_targets():
    assert parse_forward_targets("192.0.2.3:5514") == [("192.0.2.3", 5514)]
    assert parse_forward_targets("192.0.2.3:5514, 192.168.1.9:514\n10.0.0.4:6000") == [
        ("192.0.2.3", 5514), ("192.168.1.9", 514), ("192.0.2.4", 6000)]
    assert parse_forward_targets("") == []
    assert parse_forward_targets(None) == []
    assert parse_forward_targets("192.0.2.3:5514, 192.0.2.3:5514") == [("192.0.2.3", 5514)]


@pytest.mark.parametrize("bad", ["192.0.2.3", "192.0.2.3:0", "192.0.2.3:70000", "192.0.2.3:abc",
                                 "example.com:514", "192.0.2.3:5514:1", ":514", "999.1.1.1:514"])
def test_parse_rejects_bad_targets(bad):
    with pytest.raises(ValueError):
        parse_forward_targets(bad)


def test_parse_rejects_forwarding_to_itself():
    with pytest.raises(ValueError):
        parse_forward_targets("127.0.0.1:5514", own_port=5514)
    with pytest.raises(ValueError):
        parse_forward_targets("0.0.0.0:5514", own_port=5514)
    assert parse_forward_targets("127.0.0.1:5515", own_port=5514) == [("127.0.0.1", 5515)]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_every_datagram_forwarded_unchanged_to_all_targets():
    async def run():
        sinks = []
        for _ in range(2):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            s.setblocking(False)
            sinks.append(s)
        port = free_port()
        transport = await start_syslog(
            port, lambda ip, ev: None, host="127.0.0.1",
            forward=[("127.0.0.1", s.getsockname()[1]) for s in sinks])
        payloads = [b"<29>noise line", b"\xff\xfe\x00 binary", "<29>Oct 25 GWN7665[ec74d7000001] AP-STA-CONNECTED 4e:ef:65:00:00:01".encode()]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as out:
            out.bind(("127.0.0.2", 0))
            for p in payloads:
                out.sendto(p, ("127.0.0.1", port))
        await asyncio.sleep(0.3)
        got = [[s.recvfrom(9000)[0] for _ in payloads] for s in sinks]
        stats = dict(transport.get_protocol().stats)
        transport.close()
        for s in sinks:
            s.close()
        return payloads, got, stats

    payloads, got, stats = asyncio.run(run())
    assert got == [payloads, payloads]
    assert stats["forwarded"] == 6 and stats["forward_errors"] == 0


def test_unreachable_target_does_not_break_receiving():
    async def run():
        port = free_port()
        seen = []
        transport = await start_syslog(
            port, lambda ip, ev: seen.append(ev.kind), host="127.0.0.1",
            forward=[("127.0.0.1", free_port())])  # nobody listens there
        line = b"<29>x GWN7665[ec74d7000001][1][AP] daemon.notice: hostapd[1]: ath3: AP-STA-CONNECTED 4e:ef:65:00:00:01"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as out:
            out.bind(("127.0.0.2", 0))
            for _ in range(3):
                out.sendto(line, ("127.0.0.1", port))
                await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        transport.close()
        return seen

    assert asyncio.run(run()) == ["connected"] * 3


def test_parse_rejects_own_addresses_of_the_host():
    own = {"192.0.2.3", "172.30.32.1"}
    for bad in ("192.0.2.3:5514", "172.30.32.1:5514"):
        with pytest.raises(ValueError):
            parse_forward_targets(bad, own_port=5514, own_addresses=own)
    # other port or other host is fine
    assert parse_forward_targets("192.0.2.3:5515, 192.0.2.2:5514", own_port=5514, own_addresses=own) == [
        ("192.0.2.3", 5515), ("192.0.2.2", 5514)]


def test_never_forwarded_back_to_the_sender():
    async def run():
        back = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        back.bind(("127.0.0.2", 0))  # the "sender" host also listens here: a loop if we answered
        back.setblocking(False)
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        other.bind(("127.0.0.1", 0))
        other.setblocking(False)
        port = free_port()
        transport = await start_syslog(
            port, lambda ip, ev: None, host="127.0.0.1",
            forward=[("127.0.0.2", back.getsockname()[1]), ("127.0.0.1", other.getsockname()[1])])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as out:
            out.bind(("127.0.0.2", 0))
            out.sendto(b"<29>loop candidate", ("127.0.0.1", port))
        await asyncio.sleep(0.3)
        got_other = other.recvfrom(9000)[0]
        try:
            back.recvfrom(9000)
            came_back = True
        except BlockingIOError:
            came_back = False
        stats = dict(transport.get_protocol().stats)
        transport.close()
        back.close()
        other.close()
        return got_other, came_back, stats

    got_other, came_back, stats = asyncio.run(run())
    assert got_other == b"<29>loop candidate"
    assert not came_back
    assert stats["forwarded"] == 1 and stats["forward_skipped_loop"] == 1
