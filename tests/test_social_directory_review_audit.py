from app.social_directory_review import (
    update_review, read_audit, audit_for_sources,
)


def test_audit_tracks_changes_without_duplicate_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(tmp_path / "audit.json"))
    url = "https://sg.die-mitte.ch/menschen/"
    update_review("DE", url, "in_review", "Prüfung läuft", [url])
    update_review("DE", url, "in_review", "Prüfung läuft", [url])
    update_review("DE", url, "resolved", "Behoben", [url])
    events = read_audit()
    assert len(events) == 2
    assert events[0]["previous_status"] is None
    assert events[0]["new_status"] == "in_review"
    assert events[1]["previous_status"] == "in_review"
    assert events[1]["new_status"] == "resolved"
    assert events[1]["previous_comment"] == "Prüfung läuft"
    assert events[1]["comment"] == "Behoben"
    assert events[1]["changed_at"]
    assert audit_for_sources("DE", [{"url": url}]) == events
    assert audit_for_sources("FR", [{"url": url}]) == []
    assert audit_for_sources("DE", [{"url": "https://other.example/"}]) == []


def test_audit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(tmp_path / "audit.json"))
    url = "https://sg.die-mitte.ch/menschen/"
    for index in range(1002):
        update_review("DE", url, "in_review", str(index), [url])
    events = read_audit()
    assert len(events) == 1000
    assert events[0]["comment"] == "2"
    assert events[-1]["comment"] == "1001"
