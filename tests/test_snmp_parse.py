"""Tests for assembling SNMP client tables (made-up values)."""

from fast.snmp_parse import COLUMNS, MibVariant, build_clients


def test_columns_match_mibs():
    assert COLUMNS[MibVariant.AP] == {"mac": 2, "ip": 3, "bssid": 4, "ssid": 5, "signal": 6, "hostname": 9, "rate": 11}
    assert COLUMNS[MibVariant.ROUTER] == {"mac": 2, "bssid": 3, "ip": 4, "band": 6, "ssid": 7, "signal": 8, "hostname": 11}


def test_ap_rows_snr_to_rssi():
    cols = {
        "mac": {"1": "aa:bb:cc:00:00:01"}, "ip": {"1": "10.0.0.5"}, "bssid": {"1": "ec:74:d7:00:00:ab"},
        "ssid": {"1": "Home"}, "signal": {"1": "34"}, "hostname": {"1": "phone"}, "rate": {"1": "600"},
    }
    c = build_clients(MibVariant.AP, cols)["AA:BB:CC:00:00:01"]
    assert (c.snr, c.rssi, c.rate, c.bssid, c.ip, c.ssid, c.hostname, c.band) == (
        34, -61, 600, "EC:74:D7:00:00:AB", "10.0.0.5", "Home", "phone", None)


def test_router_mac_without_colons_and_dbm():
    cols = {"mac": {"1": "aabbcc000001"}, "bssid": {"1": "ec74d7000002"}, "signal": {"1": "-22"}, "band": {"1": "2.4GHZ"}}
    c = build_clients(MibVariant.ROUTER, cols)["AA:BB:CC:00:00:01"]
    assert (c.rssi, c.snr, c.band, c.bssid) == (-22, None, "2.4G", "EC:74:D7:00:00:02")


def test_row_with_empty_bssid_dropped():
    cols = {"mac": {"1": "aa:bb:cc:00:00:01", "2": "aa:bb:cc:00:00:02"}, "bssid": {"1": "", "2": "ec:74:d7:00:00:ab"}}
    assert list(build_clients(MibVariant.AP, cols)) == ["AA:BB:CC:00:00:02"]


def test_row_missing_from_mac_column_dropped():
    cols = {"mac": {"1": "aa:bb:cc:00:00:01"}, "bssid": {"1": "ec:74:d7:00:00:ab", "2": "ec:74:d7:00:00:ab"}}
    assert list(build_clients(MibVariant.AP, cols)) == ["AA:BB:CC:00:00:01"]


def test_non_numeric_signal_gives_none():
    cols = {"mac": {"1": "aa:bb:cc:00:00:01"}, "bssid": {"1": "ec:74:d7:00:00:ab"}, "signal": {"1": "x"}}
    c = build_clients(MibVariant.AP, cols)["AA:BB:CC:00:00:01"]
    assert (c.snr, c.rssi) == (None, None)
