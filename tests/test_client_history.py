"""Tests for the per-client history behind the detail panel (made-up values)."""

from datetime import datetime, timedelta

from client_history import ClientHistory

T0 = datetime(2026, 1, 1, 12, 0, 0)
PHONE = "4E:EF:65:00:00:01"


def at(s):
    return T0 + timedelta(seconds=s)


def c(ap="A", band="5G", rssi=-60, connected=True, source="snmp", rate=600):
    return {"connected": connected, "apId": ap, "apName": f"AP {ap}", "channelClassStr": band,
            "rssi": rssi, "txRate": rate, "fastSource": source}


def test_sample_only_on_change_or_after_gap():
    h = ClientHistory(gap=timedelta(seconds=15))
    h.record({PHONE: c()}, at(0))
    h.record({PHONE: c()}, at(5))
    h.record({PHONE: c(rssi=-62)}, at(6))
    h.record({PHONE: c(rssi=-62)}, at(25))
    samples = h.get(PHONE)["samples"]
    assert [(s["t"], s["rssi"]) for s in samples] == [(at(0).isoformat(), -60), (at(6).isoformat(), -62), (at(25).isoformat(), -62)]
    assert samples[0]["source"] == "snmp" and samples[0]["ap_name"] == "AP A" and samples[0]["rate"] == 600


def test_forced_sample_for_live_measurement():
    h = ClientHistory()
    h.record({PHONE: c()}, at(0))
    h.add_sample(PHONE, c(), at(1), source="live")
    assert [s["source"] for s in h.get(PHONE)["samples"]] == ["snmp", "live"]


def test_events_connect_roam_band_disconnect():
    h = ClientHistory()
    h.record({PHONE: c(connected=False)}, at(0))
    h.record({PHONE: c()}, at(1))
    h.record({PHONE: c(ap="B")}, at(2))
    h.record({PHONE: c(ap="B", band="6G")}, at(3))
    h.record({PHONE: c(ap="B", band="6G", connected=False, source="syslog")}, at(4))
    ev = h.get(PHONE)["events"]
    assert [(e["kind"], e.get("ap_name"), e.get("band")) for e in ev] == [
        ("connected", "AP A", "5G"), ("roamed", "AP B", "5G"), ("band", "AP B", "6G"), ("disconnected", "AP B", "6G")]
    assert ev[1]["from_ap_name"] == "AP A" and ev[3]["source"] == "syslog"


def test_limits():
    h = ClientHistory(max_samples=3, max_events=2, gap=timedelta(seconds=0))
    for i in range(10):
        h.record({PHONE: c(rssi=-50 - i, ap="A" if i % 2 else "B")}, at(i))
    got = h.get(PHONE)
    assert len(got["samples"]) == 3 and len(got["events"]) == 2


def test_unknown_client_empty():
    assert ClientHistory().get(PHONE) == {"samples": [], "events": []}
