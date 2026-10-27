"""Quality trend metrics distinguish old reports from recorded quality snapshots."""
import pytest
from app.social_check_history import save_report
from app.social_quality_trends import quality_trends


def test_quality_trends_with_legacy_runs_and_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_CHECK_HISTORY_PATH", str(tmp_path / "history.sqlite3"))
    assert quality_trends("DE")["trend"] == []
    save_report("DE", {"checked_at": "2026-10-25", "results": [
        {"person_number": "1", "platform": "Instagram",
         "url": "https://instagram.com/one", "status": "reachable", "http_status": 200}]})
    save_report("DE", {"checked_at": "2026-10-26", "results": [
        {"person_number": "1", "platform": "Instagram",
         "url": "https://instagram.com/one", "status": "possibly_missing", "http_status": 404},
        {"person_number": "2", "platform": "Instagram",
         "url": "https://instagram.com/two", "status": "reachable", "http_status": 200}],
        "quality_snapshot": {"checked": 3, "invalid": 1, "duplicates": 2}})
    points = quality_trends("DE")["trend"]
    assert len(points) == 2
    assert points[0]["reachable_percent"] == 100
    assert points[0]["invalid"] is None
    assert points[1]["reachable_percent"] == 50
    assert points[1]["flagged"] == 1
    assert points[1]["flagged_percent"] == 50
    assert points[1]["invalid"] == 1
    assert points[1]["duplicates"] == 2
    assert quality_trends("DE", limit=1)["runs"] == 1


def test_empty_results_and_invalid_parameters(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_CHECK_HISTORY_PATH", str(tmp_path / "history.sqlite3"))
    save_report("FR", {"checked_at": "2026-10-27", "results": []})
    point = quality_trends("FR")["trend"][0]
    assert point["reachable_percent"] is None
    assert point["flagged_percent"] is None
    for language, limit in (("XX", 10), ("DE", 0), ("DE", 31), ("DE", True)):
        with pytest.raises(ValueError):
            quality_trends(language, limit=limit)
