"""Tests for read-only system health."""
from datetime import datetime, timezone, timedelta
import pytest
from app import main
from app.social_data_health import data_health
from app.social_check_history import save_report

def test_health_freshness_and_quality(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_CHECK_HISTORY_PATH", str(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(main, "_profile_snapshot_path", tmp_path / "snapshot.json")
    main.save_profile_snapshot({"DE": {"profiles": {
        "1": {"Instagram": "https://instagram.com/test"},
        "2": {"Instagram": "https://instagram.com/test?ref=abc"}}}})
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    before = data_health("DE", now=now)
    assert before["stale"] and before["verified_links"] == 2
    assert before["quality"]["duplicates"] == 2
    save_report("DE", {"checked_at": "2026-10-27T11:00:00+00:00", "results": []})
    after = data_health("DE", now=now)
    assert not after["stale"] and after["last_check_age_hours"] == 1
    assert data_health("DE", now=now + timedelta(days=3))["stale"]
    with pytest.raises(ValueError):
        data_health("DE", stale_hours=0)
    with pytest.raises(ValueError):
        data_health("XX")
