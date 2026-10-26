"""Tests for the SNMP poller with an injected walk function."""

import asyncio

import pytest

from fast.snmp import SnmpPoller, SnmpUnavailable
from fast.snmp_parse import MibVariant, TABLES


def fake_walk(tree: dict[str, dict[str, str]], calls: list[str] | None = None):
    async def walk(host: str, community: str, oid: str) -> dict[str, str]:
        if calls is not None:
            calls.append(oid)
        if isinstance(tree, Exception):
            raise tree
        return tree.get(oid, {})
    return walk


AP_INFO = "1.3.6.1.4.1.42397.1.1.2"
ROUTER_INFO = "1.3.6.1.4.1.42397.1.2.1"


def test_detect_ap_even_without_clients():
    p = SnmpPoller("h", "public", walk=fake_walk({AP_INFO: {"1.0": "GWN7665"}}))
    assert asyncio.run(p.detect()) is MibVariant.AP
    assert p.variant is MibVariant.AP


def test_detect_router():
    p = SnmpPoller("h", "public", walk=fake_walk({ROUTER_INFO: {"1.1.1.1": "1"}}))
    assert asyncio.run(p.detect()) is MibVariant.ROUTER


def test_detect_none():
    p = SnmpPoller("h", "public", walk=fake_walk({}))
    assert asyncio.run(p.detect()) is None


def test_detect_timeout_gives_none():
    p = SnmpPoller("h", "public", walk=fake_walk(TimeoutError()))
    assert asyncio.run(p.detect()) is None


def test_poll_walks_only_its_columns():
    calls: list[str] = []
    t = TABLES[MibVariant.AP]
    tree = {AP_INFO: {"1.0": "x"}, f"{t}.2": {"1": "aa:bb:cc:00:00:01"}, f"{t}.4": {"1": "ec:74:d7:00:00:ab"}}
    p = SnmpPoller("h", "public", walk=fake_walk(tree, calls))
    asyncio.run(p.detect())
    calls.clear()
    clients = asyncio.run(p.poll())
    assert list(clients) == ["AA:BB:CC:00:00:01"]
    assert sorted(calls) == sorted(f"{t}.{n}" for n in (2, 4, 6, 11))


def test_poll_timeout_raises_unavailable():
    p = SnmpPoller("h", "public", walk=fake_walk(TimeoutError()))
    p.variant = MibVariant.AP
    with pytest.raises(SnmpUnavailable):
        asyncio.run(p.poll())


def test_detect_unexpected_error_gives_none():
    p = SnmpPoller("h", "public", walk=fake_walk(ValueError("odd reply")))
    assert asyncio.run(p.detect()) is None


def test_poll_unexpected_error_raises_unavailable():
    p = SnmpPoller("h", "public", walk=fake_walk(ValueError("odd reply")))
    p.variant = MibVariant.AP
    with pytest.raises(SnmpUnavailable):
        asyncio.run(p.poll())


def fake_next(tree: dict[str, dict[str, str]], calls: list[str] | None = None):
    """GETNEXT over a tree of {column oid: {row: value}}, like an agent."""
    flat = {f"{prefix}.{row}": value for prefix, rows in tree.items() for row, value in rows.items()}
    key = lambda oid: tuple(int(p) for p in oid.split("."))
    ordered = sorted(flat, key=key)

    async def next_(host: str, community: str, oid: str):
        if calls is not None:
            calls.append(oid)
        for name in ordered:
            if key(name) > key(oid):
                return name, flat[name]
        return None
    return next_


def client_tree(rows: dict[str, tuple[str, int]]):
    t = TABLES[MibVariant.AP]
    return {
        f"{t}.2": {r: m for r, (m, _) in rows.items()},
        f"{t}.4": {r: "ec:74:d7:00:00:ab" for r in rows},
        f"{t}.6": {r: str(s) for r, (_, s) in rows.items()},
        f"{t}.11": {r: "600" for r in rows},
    }


def make_ap(tree, calls=None):
    p = SnmpPoller("h", "public", walk=fake_walk({**tree, AP_INFO: {"1.0": "x"}}), next_=fake_next(tree, calls))
    asyncio.run(p.detect())
    return p


def test_poll_client_reads_only_its_row_and_remembers_it():
    rows = {"1": ("aa:bb:cc:00:00:01", 30), "2": ("aa:bb:cc:00:00:02", 40), "3": ("aa:bb:cc:00:00:03", 50)}
    calls: list[str] = []
    p = make_ap(client_tree(rows), calls)
    client = asyncio.run(p.poll_client("AA:BB:CC:00:00:02"))
    assert (client.mac, client.snr, client.rssi, client.rate) == ("AA:BB:CC:00:00:02", 40, -55, 600)
    first = len(calls)
    asyncio.run(p.poll_client("AA:BB:CC:00:00:02"))
    # Second time: no search through the MAC column, one step per column.
    assert len(calls) - first == 4


def test_poll_client_finds_it_again_after_rows_shifted():
    rows = {"1": ("aa:bb:cc:00:00:01", 30), "2": ("aa:bb:cc:00:00:02", 40)}
    p = make_ap(client_tree(rows))
    assert asyncio.run(p.poll_client("AA:BB:CC:00:00:02")).snr == 40
    # First client left: the row of the second is now the first one.
    p._next = fake_next(client_tree({"2": ("aa:bb:cc:00:00:02", 45)}))
    assert asyncio.run(p.poll_client("AA:BB:CC:00:00:02")).snr == 45


def test_poll_client_not_here_is_none():
    p = make_ap(client_tree({"1": ("aa:bb:cc:00:00:01", 30)}))
    assert asyncio.run(p.poll_client("AA:BB:CC:00:00:99")) is None


def test_poll_client_timeout_raises_unavailable():
    async def broken(host, community, oid):
        raise TimeoutError()
    p = make_ap(client_tree({"1": ("aa:bb:cc:00:00:01", 30)}))
    p._next = broken
    with pytest.raises(SnmpUnavailable):
        asyncio.run(p.poll_client("AA:BB:CC:00:00:01"))
