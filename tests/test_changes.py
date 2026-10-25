"""Tests for the per-client change tracking behind the card flash."""

from datetime import datetime, timedelta

from changes import ChangeTracker, signal_step

T0 = datetime(2026, 1, 1, 12, 0, 0)


def test_signal_steps_match_card_colours():
    assert [signal_step(v) for v in (-50, -60, -61, -70, -71, -80, -81, None)] == [3, 3, 2, 2, 1, 1, 0, None]


def test_first_update_records_nothing():
    t = ChangeTracker()
    t.update({"A": {"connected": True, "apId": "X", "rssi": -50}}, T0)
    assert t.changes == {}


def test_connect_roam_disconnect_and_signal_step():
    t = ChangeTracker()
    t.update({"A": {"connected": False}}, T0)
    t.update({"A": {"connected": True, "apId": "X", "rssi": -50}}, T0 + timedelta(seconds=1))
    assert t.changes["A"] == ("connected", T0 + timedelta(seconds=1))
    t.update({"A": {"connected": True, "apId": "Y", "rssi": -50}}, T0 + timedelta(seconds=2))
    assert t.changes["A"][0] == "roamed"
    t.update({"A": {"connected": True, "apId": "Y", "rssi": -55}}, T0 + timedelta(seconds=3))
    assert t.changes["A"][0] == "roamed"  # same colour step: no new change
    t.update({"A": {"connected": True, "apId": "Y", "rssi": -75}}, T0 + timedelta(seconds=4))
    assert t.changes["A"] == ("signal", T0 + timedelta(seconds=4))
    t.update({"A": {"connected": False, "apId": "Y"}}, T0 + timedelta(seconds=5))
    assert t.changes["A"][0] == "disconnected"


def test_missing_client_counts_as_disconnected():
    t = ChangeTracker()
    t.update({"A": {"connected": True, "apId": "X"}}, T0)
    t.update({}, T0 + timedelta(seconds=1))
    assert t.changes["A"][0] == "disconnected"
