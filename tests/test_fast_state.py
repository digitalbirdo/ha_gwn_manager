"""Tests for the fast-source overlay (made-up MACs)."""

from datetime import datetime, timedelta

from fast.snmp_parse import SnmpClient
from fast.state import FastState
from fast.syslog_parse import SyslogEvent

T0 = datetime(2026, 1, 1, 12, 0, 0)
AGE = timedelta(seconds=30)
PHONE = "4E:EF:65:00:00:01"
AP_A, AP_B = "EC:74:D7:00:00:0A", "EC:74:D7:00:00:0B"


def snmp(rssi=-60, bssid="EC:74:D7:00:00:AB", rate=600):
    return {PHONE: SnmpClient(PHONE, bssid, None, None, rssi, rssi + 95, rate, None, None)}


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def test_syslog_connect_then_disconnect():
    s = FastState()
    assert s.apply_syslog(AP_A, SyslogEvent("connected", PHONE, ifname="ath3"), at(0))
    assert s.overlay(at(1), AGE)[PHONE]["connected"] is True
    assert s.overlay(at(1), AGE)[PHONE]["apId"] == AP_A
    s.apply_syslog(AP_A, SyslogEvent("disconnected", PHONE), at(2))
    assert s.overlay(at(3), AGE)[PHONE]["connected"] is False


def test_snmp_after_syslog_keeps_ap_and_adds_signal():
    s = FastState()
    s.apply_syslog(AP_A, SyslogEvent("connected", PHONE), at(0))
    s.apply_snmp(AP_A, snmp(), at(5), started=at(3))
    o = s.overlay(at(6), AGE)[PHONE]
    assert (o["apId"], o["rssi"], o["snr"], o["txRate"], o["bssid"]) == (AP_A, -60, 35, 600, "EC:74:D7:00:00:AB")


def test_roam_without_disconnect():
    s = FastState()
    s.apply_syslog(AP_A, SyslogEvent("connected", PHONE), at(0))
    s.apply_syslog(AP_B, SyslogEvent("connected", PHONE), at(5))
    o = s.overlay(at(6), AGE)[PHONE]
    assert (o["connected"], o["apId"]) == (True, AP_B)


def test_late_disconnect_from_old_ap_ignored_after_roam():
    s = FastState()
    s.apply_syslog(AP_B, SyslogEvent("connected", PHONE), at(5))
    assert not s.apply_syslog(AP_A, SyslogEvent("disconnected", PHONE), at(6))
    assert s.overlay(at(7), AGE)[PHONE]["connected"] is True


def test_old_entries_expire():
    s = FastState()
    s.apply_syslog(AP_A, SyslogEvent("connected", PHONE), at(0))
    assert PHONE not in s.overlay(at(31), AGE)


def test_stale_snmp_poll_does_not_undo_newer_roam():
    s = FastState()
    s.apply_syslog(AP_B, SyslogEvent("connected", PHONE), at(10))
    s.apply_snmp(AP_A, snmp(), at(12), started=at(2))
    assert s.overlay(at(13), AGE)[PHONE]["apId"] == AP_B


def test_band_learned_from_bssid():
    s = FastState()
    s.learn_band("EC:74:D7:00:00:AB", "6G")
    s.apply_snmp(AP_A, snmp(), at(5), started=at(3))
    assert s.overlay(at(6), AGE)[PHONE]["channelClassStr"] == "6G"


def test_router_snmp_band_used_directly():
    s = FastState()
    c = SnmpClient(PHONE, "EC:74:D7:00:00:01", None, None, -22, None, None, "2.4G", None)
    s.apply_snmp(AP_A, {PHONE: c}, at(5), started=at(3))
    assert s.overlay(at(6), AGE)[PHONE]["channelClassStr"] == "2.4G"


def test_debug_connect_carries_values():
    s = FastState()
    s.apply_syslog(AP_A, SyslogEvent("connected", PHONE, rssi=-66, rate=432, channel=85, debug=True), at(0))
    o = s.overlay(at(1), AGE)[PHONE]
    assert (o["rssi"], o["txRate"], o["channel"]) == (-66, 432, 85)


def test_unchanged_snmp_reports_no_change():
    s = FastState()
    assert s.apply_snmp(AP_A, snmp(), at(5), started=at(3))
    assert not s.apply_snmp(AP_A, snmp(), at(15), started=at(13))
    assert s.apply_snmp(AP_A, snmp(rssi=-70), at(25), started=at(23))


def test_apply_overlay_updates_and_creates_clients():
    from fast.state import apply_overlay

    aps = {AP_A: {"name": "AP A"}, AP_B: {"name": "AP B"}}
    clients = {
        PHONE: {"clientId": PHONE, "connected": True, "apId": AP_A, "apName": "AP A", "rssi": -80, "name": "Phone"},
        "AA:00:00:00:00:02": {"clientId": "AA:00:00:00:00:02", "connected": True, "apId": AP_A},
    }
    overlay = {
        PHONE: {"connected": True, "apId": AP_B, "rssi": -55, "source": "syslog"},
        "AA:00:00:00:00:02": {"connected": False, "apId": AP_A, "source": "syslog"},
        "AA:00:00:00:00:03": {"connected": True, "apId": AP_A, "rssi": -40, "source": "snmp"},
    }
    connected = apply_overlay(clients, aps, overlay)
    assert clients[PHONE] | {} == {**clients[PHONE]} and clients[PHONE]["apId"] == AP_B
    assert (clients[PHONE]["apName"], clients[PHONE]["rssi"], clients[PHONE]["name"], clients[PHONE]["fastSource"]) == ("AP B", -55, "Phone", "syslog")
    assert clients["AA:00:00:00:00:02"]["connected"] is False
    assert clients["AA:00:00:00:00:03"]["connected"] is True and clients["AA:00:00:00:00:03"]["wired"] == 2
    assert connected == {PHONE, "AA:00:00:00:00:03"}


def test_apply_overlay_disconnect_of_unknown_client_is_ignored():
    from fast.state import apply_overlay

    clients: dict = {}
    assert apply_overlay(clients, {}, {PHONE: {"connected": False, "apId": AP_A}}) == set()
    assert clients == {}
