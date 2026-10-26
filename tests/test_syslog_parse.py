"""Tests for the syslog line parser (made-up MACs and IPs)."""

from fast.syslog_parse import SyslogEvent, parse_syslog_line

HEAD = "<29>Oct 25 13:14:47 GWN7665[ec74d7000001][1.0.27.20][AP X] daemon.notice: hostapd[1]: [.6]: "


def test_connected():
    e = parse_syslog_line(HEAD + "ath3: AP-STA-CONNECTED 4e:ef:65:00:00:01")
    assert e == SyslogEvent("connected", "4E:EF:65:00:00:01", ifname="ath3", device_tag="EC74D7000001")


def test_disconnected():
    e = parse_syslog_line(HEAD + "ath1: AP-STA-DISCONNECTED 4e:ef:65:00:00:01")
    assert (e.kind, e.client_mac, e.ifname) == ("disconnected", "4E:EF:65:00:00:01", "ath1")


def test_freed_has_bssid():
    e = parse_syslog_line(HEAD + "Sta is freed: sta=4e:ef:65:00:00:01, bssid=ec:74:d7:00:00:ab, if=ath3, if_idx=0")
    assert (e.kind, e.bssid, e.ifname) == ("disconnected", "EC:74:D7:00:00:AB", "ath3")


def test_dhcpack_with_hostname():
    e = parse_syslog_line("<30>Oct 25 13:14:47 GWN7062[ec74d7000009][1.0.9.58] daemon.info: dnsmasq-dhcp[1]: DHCPACK(br-lan) 192.0.2.9 4e:ef:65:00:00:01 iPhone")
    assert (e.kind, e.ip, e.hostname, e.device_tag) == ("dhcp", "192.0.2.9", "iPhone", "EC74D7000009")


def test_dhcpack_without_hostname():
    e = parse_syslog_line("<30>Oct 25 13:14:47 GWN7062[ec74d7000009][1.0.9.58] daemon.info: dnsmasq-dhcp[1]: DHCPACK(br-lan) 192.0.2.9 4e:ef:65:00:00:01")
    assert (e.ip, e.hostname) == ("192.0.2.9", None)


GSEVENT = '<15>Oct 25 13:14:47 GWN7665[ec74d7000001][1.0.27.20][AP X] daemon.debug: controller: process_send_events: gseventlog send '


def test_gseventlog_connect_only_when_enabled():
    line = GSEVENT + '{ "event_content": { "client_mac": "4eef65000001", "ap_mac": "ec74d7000001", "ssid": "S", "status": 1, "channel": 85, "rssi": -66, "tx_rate": 432, "rx_rate": 432, "new_sta_mode": "11AXA_HE80", "frequency band": 6 }, "event_subtype": 1 }'
    assert parse_syslog_line(line) is None
    e = parse_syslog_line(line, debug_events=True)
    assert (e.kind, e.client_mac, e.ap_mac, e.rssi, e.rate, e.channel, e.sta_mode, e.debug) == (
        "connected", "4E:EF:65:00:00:01", "EC:74:D7:00:00:01", -66, 432, 85, "11AXA_HE80", True)


def test_gseventlog_info_and_disconnect():
    info = parse_syslog_line(GSEVENT + '{ "event_content": { "client_mac": "4eef65000001", "os": "iOS", "hostname": "iPhone", "ipv4": "192.0.2.9" } }', debug_events=True)
    assert (info.kind, info.hostname, info.os, info.ip) == ("info", "iPhone", "iOS", "192.0.2.9")
    gone = parse_syslog_line(GSEVENT + '{ "event_content": { "client_mac": "4eef65000001", "ap_mac": "ec74d7000001", "ssid": "S" }, "event_subtype": 0 }', debug_events=True)
    assert (gone.kind, gone.ap_mac) == ("disconnected", "EC:74:D7:00:00:01")


def test_gseventlog_truncated_json_returns_none():
    assert parse_syslog_line(GSEVENT + '{ "event_content": { "client_mac": "4eef', debug_events=True) is None


def test_noise_returns_none():
    assert parse_syslog_line(HEAD + "receive nbr msg roam cache update for sta 4e:ef:65:00:00:01") is None
    assert parse_syslog_line("") is None
    assert parse_syslog_line("\xff garbage") is None
