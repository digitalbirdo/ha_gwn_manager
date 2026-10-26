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


# Diagnostic lines (real formats, made-up MACs).
HEAD_DBG = "<31>Oct 25 15:04:24 GWN7665[ec74d7000001][1.0.27.20][AP OG] daemon.debug: controller: "
P = "4e:ef:65:00:00:01"


def test_fast_roaming_without_debug():
    e = parse_syslog_line(HEAD + f"sta {P} 80211r fast roaming: ec:74:d7:00:00:26 --> ec:74:d7:00:00:aa")
    assert (e.kind, e.diag, e.client_mac, e.from_bssid, e.bssid, e.debug) == (
        "diag", "roam_fast", "4E:EF:65:00:00:01", "EC:74:D7:00:00:26", "EC:74:D7:00:00:AA", False)


def test_init_assoc_roam():
    e = parse_syslog_line(HEAD + f"sta {P} 80211r init assoc: 00:00:00:00:00:00 --> ec:74:d7:00:00:f9")
    assert (e.diag, e.from_bssid, e.bssid) == ("roam_init", "00:00:00:00:00:00", "EC:74:D7:00:00:F9")


def test_roaming_away_has_reason_and_interface():
    e = parse_syslog_line(HEAD + f"gs_sta_roaming_away: addr={P}, if=ath3, reason_code=201")
    assert (e.diag, e.client_mac, e.ifname, e.reason_code) == ("roam_away", "4E:EF:65:00:00:01", "ath3", 201)


def test_debug_diagnostics_only_when_enabled():
    line = HEAD_DBG + f"log_parse: network-health-revice:{P} disassociation received  1792936823 801937"
    assert parse_syslog_line(line) is None
    e = parse_syslog_line(line, debug_events=True)
    assert (e.kind, e.diag, e.client_mac, e.debug) == ("diag", "disassoc", "4E:EF:65:00:00:01", True)


def test_auth_and_assoc_status_only_when_not_zero():
    ok = HEAD_DBG + f"log_parse: network-health-revice:{P} send auth reply status_code 0 1792936823"
    assert parse_syslog_line(ok, debug_events=True) is None
    bad = HEAD_DBG + f"log_parse: network-health-revice:{P} send assoc resp status_code 17 1792936823"
    e = parse_syslog_line(bad, debug_events=True)
    assert (e.diag, e.status_code) == ("assoc_fail", 17)
    bad = HEAD_DBG + f"log_parse: network-health-revice:{P} send auth reply status_code 1 1792936823"
    assert parse_syslog_line(bad, debug_events=True).diag == "auth_fail"


def test_pingpong_and_roam_statistics():
    e = parse_syslog_line(HEAD_DBG + f"count_roam_info_for_client: network_health {P} PINGPONG_ROAM", debug_events=True)
    assert (e.diag, e.client_mac) == ("pingpong", "4E:EF:65:00:00:01")
    zero = HEAD_DBG + f"count_roam_info_for_client: network_health_roam_statics {P} QUALITY_FAILED 0 STABILITY_FAILED 0 LATENCY_FAILED 0"
    assert parse_syslog_line(zero, debug_events=True) is None
    e = parse_syslog_line(zero.replace("QUALITY_FAILED 0", "QUALITY_FAILED 2"), debug_events=True)
    assert (e.diag, e.detail) == ("roam_stats", "quality=2 stability=0 latency=0")


def test_steering_request_and_response():
    req = "<15>Oct 25 15:00:05 GWN7665[ec74d7000001][1.0.27.20][AP EG] user.debug: : gsdcs - dcs_cmn_handle_steer_req: Received steer request from AP ec:74:d7:00:00:24 for STA " + P + ", steer type STICKY, STA type BTM, RSSI 19, channel 40, ssid Finkennest"
    e = parse_syslog_line(req, debug_events=True)
    assert (e.diag, e.client_mac, e.snr, e.channel, e.detail) == ("steer_req", "4E:EF:65:00:00:01", 19, 40, "STICKY BTM")
    resp = "<15>Oct 25 15:00:05 GWN7665[ec74d7000001][1.0.27.20][AP EG] user.debug: : gsdcs - dcs_send_steer_resp: Send steer response to AP ec:74:d7:00:00:24, for STA " + P + " result code NO CANDIDATE, steer type STICKY, STA type BTM,channel 255, channel utilization 255"
    e = parse_syslog_line(resp, debug_events=True)
    assert (e.diag, e.result, e.detail) == ("steer_resp", "NO CANDIDATE", "STICKY BTM")
