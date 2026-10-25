"""Tests for removing old clients."""

from datetime import datetime, timedelta, timezone

from cleanup import is_stale

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def test_off_when_days_zero():
    assert not is_stale(False, NOW - timedelta(days=400), None, NOW, 0)


def test_connected_never_stale():
    assert not is_stale(True, NOW - timedelta(days=400), None, NOW, 30)


def test_older_than_limit_is_stale():
    assert is_stale(False, NOW - timedelta(days=31), None, NOW, 30)
    assert not is_stale(False, NOW - timedelta(days=29), None, NOW, 30)


def test_newest_of_last_seen_and_last_connected_counts():
    assert not is_stale(False, NOW - timedelta(days=90), NOW - timedelta(days=1), NOW, 30)


def test_unknown_age_is_not_stale():
    assert not is_stale(False, None, None, NOW, 30)
