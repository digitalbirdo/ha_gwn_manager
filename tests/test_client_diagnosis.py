"""Tests for the roaming diagnosis of one client (made-up values)."""

from datetime import datetime, timedelta

from client_diagnosis import DiagnosisLog, analyze, annotate, bssid_map

T0 = datetime(2026, 1, 1, 12, 0, 0)
PHONE = "4E:EF:65:00:00:01"
A, B = "EC:74:D7:00:00:01", "EC:74:D7:00:00:02"


def at(s):
    return T0 + timedelta(seconds=s)


def log_of(*items):
    """items: (seconds, event dict)"""
    log = DiagnosisLog()
    for s, event in items:
        log.add(PHONE, event, at(s))
    return log.get(PHONE)


def con(ap, ifname="ath4"):
    return {"type": "connected", "ap": ap, "ifname": ifname}


def test_same_event_from_two_lines_is_kept_once_and_merged():
    events = log_of(
        (0, {"type": "disconnected", "ap": A, "ifname": "ath4"}),
        (1, {"type": "disconnected", "ap": A, "bssid": "EC:74:D7:00:00:AA"}),
        (10, {"type": "disconnected", "ap": A}),
    )
    assert len(events) == 2
    assert events[0]["bssid"] == "EC:74:D7:00:00:AA" and events[0]["ifname"] == "ath4"


def test_steering_storm_is_counted_in_one_entry():
    steer = {"type": "steer_resp", "ap": A, "result": "NO CANDIDATE", "detail": "STICKY BTM"}
    events = log_of(*((s, steer) for s in range(0, 50, 5)), (200, steer))
    assert [e.get("count", 1) for e in events] == [10, 1]
    assert analyze(events, [], at(300))["stats"]["steer_no_candidate"] == 11


def test_old_events_are_dropped():
    log = DiagnosisLog(max_age=timedelta(hours=1))
    log.add(PHONE, con(A), at(0))
    log.add(PHONE, con(B), at(7200))
    assert [e["ap"] for e in log.get(PHONE)] == [B]


def test_roam_types_and_band_change():
    events = log_of(
        (0, con(A, "ath4")),
        (100, con(B, "ath4")),
        (100, {"type": "roam_fast", "ap": B, "from_bssid": "EC:74:D7:00:00:A1", "bssid": "EC:74:D7:00:00:B1"}),
        (200, con(A, "ath4")),
        (200, {"type": "roam_init", "ap": A, "from_bssid": "EC:74:D7:00:00:B1", "bssid": "EC:74:D7:00:00:A1"}),
        (300, con(A, "ath2")),
    )
    stats = analyze(events, [], at(400))["stats"]
    assert (stats["connects"], stats["roams"], stats["roams_fast"], stats["roams_init"], stats["band_changes"]) == (4, 2, 1, 1, 1)
    assert stats["per_ap"] == {A: 3, B: 1}


def test_first_association_is_not_a_roam():
    events = log_of(
        (0, con(A)),
        (0, {"type": "roam_init", "ap": A, "from_bssid": "00:00:00:00:00:00", "bssid": "EC:74:D7:00:00:A1"}),
    )
    assert analyze(events, [], at(100))["stats"]["roams"] == 0


def test_pingpong_within_a_minute():
    events = log_of((0, con(A)), (20, con(B)), (40, con(A)), (60, con(B)))
    stats = analyze(events, [], at(100))["stats"]
    assert stats["roams"] == 3 and stats["pingpong"] == 2
    events = log_of((0, con(A)), (20, con(B)), (400, con(A)))
    assert analyze(events, [], at(500))["stats"]["pingpong"] == 0


def test_drop_with_cause():
    samples = [{"t": at(0).isoformat(), "rssi": -82, "ap": A}]
    events = log_of(
        (0, con(A)),
        (50, {"type": "disconnected", "ap": A}),
        (200, con(A)),
        (300, {"type": "disassoc", "ap": A}),
        (301, {"type": "disconnected", "ap": A}),
        (320, con(B)),  # back within 30 s: not a drop
        (400, {"type": "disconnected", "ap": B}),
    )
    result = analyze(events, samples, at(1000))
    assert [(d["t"], d["cause"]) for d in result["drops"]] == [(at(50).isoformat(), "weak_signal"), (at(400).isoformat(), "unknown")]


def test_recent_disconnect_is_not_yet_a_drop():
    events = log_of((0, con(A)), (50, {"type": "disconnected", "ap": A}))
    assert analyze(events, [], at(60))["stats"]["drops"] == 0


def ids(result):
    return {f["id"]: f["confidence"] for f in result["findings"]}


def test_sticky_and_steering_findings():
    samples = [{"t": at(i).isoformat(), "rssi": -85, "ap": A} for i in range(10)]
    steer = {"type": "steer_resp", "ap": A, "result": "NO CANDIDATE", "detail": "STICKY BTM"}
    events = log_of((0, con(A)), *((s, steer) for s in range(0, 500, 70)))
    found = ids(analyze(events, samples, at(600), debug_events=True))
    assert found["sticky"] == "high" and "steer_no_target" not in found
    events = log_of((0, con(A)), *((s, steer) for s in range(0, 1000, 70)))
    assert ids(analyze(events, samples, at(1100), debug_events=True))["steer_no_target"] == "high"
    # Good signal: steering without a target is no sign of a sticky client.
    good = [{"t": at(i).isoformat(), "rssi": -55, "ap": A} for i in range(10)]
    found = ids(analyze(events, good, at(1100), debug_events=True))
    assert "sticky" not in found and found["steer_no_target"] == "medium"


def test_no_fast_roaming_finding():
    items = []
    for i in range(4):
        ap = A if i % 2 else B
        items += [(i * 100, con(ap)), (i * 100, {"type": "roam_init", "ap": ap, "from_bssid": "EC:74:D7:00:00:C1", "bssid": "EC:74:D7:00:00:C2"})]
    result = analyze(log_of(*items), [], at(1000), debug_events=True)
    assert result["stats"]["roams_init"] == 3 and ids(result)["no_fast_roaming"] == "medium"


def test_hints_without_debug_and_for_the_router():
    assert ids(analyze([], [], at(10))) == {"need_debug": "low"}
    assert ids(analyze([], [], at(10), debug_events=True, blind_aps={A}, current_ap=A)) == {"router_blind": "low"}


def test_annotate_and_bssid_map():
    aps = {A: {"info": {"radioFrequencyInfo": {"radioFrequency5g": {"bssid5gs": ["ec:74:d7:00:00:a5"], "channel": "36"}}}}}
    from fast.state import bssid_table
    bssids = bssid_map(aps, bssid_table)
    assert bssids["EC:74:D7:00:00:A5"] == {"channelClassStr": "5G", "channel": 36, "ap": A}
    events = log_of(
        (0, {"type": "roam_fast", "ap": B, "from_bssid": "EC:74:D7:00:00:A5", "bssid": "EC:74:D7:00:00:B5"}),
        (1, con(B)),
    )
    samples = [{"t": at(2).isoformat(), "ap": B, "band": "6G"}]
    events = annotate(events, samples, bssids)
    assert (events[0]["from_ap"], events[0]["from_band"]) == (A, "5G")
    assert events[1]["band"] == "6G"
