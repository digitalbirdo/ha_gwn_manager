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
