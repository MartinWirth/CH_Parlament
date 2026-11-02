from app.social_directory_trends import annotate_directory_failures


def test_review_queue_prioritizes_persistent_failures_and_tracks_history():
    previous = {"sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                              "failure_streak": 2, "status": "error",
                              "last_success_at": "2026-10-01T00:00:00+00:00",
                              "failure_history": [{"checked_at": "earlier", "reason": "timeout"}]}]}
    current = {"checked_at": "2026-10-31T00:00:00+00:00", "sources": [
        {"url": "https://tg.die-mitte.ch/menschen/", "status": "parsed", "matched_members": 0},
        {"url": "https://sg.die-mitte.ch/menschen/", "status": "error", "reason": "HTTP 404"}]}
    annotate_directory_failures(current, previous)
    assert [x["priority"] for x in current["review_queue"]] == ["high", "low"]
    high = current["review_queue"][0]
    assert high["failure_streak"] == 3
    assert high["last_success_at"] == "2026-10-01T00:00:00+00:00"
    assert len(high["failure_history"]) == 2
    assert high["failure_history"][-1]["reason"] == "HTTP 404"


def test_recovery_updates_last_success_and_clears_warning():
    previous = {"sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                              "failure_streak": 4, "status": "error"}]}
    current = {"checked_at": "2026-10-31T00:00:00+00:00", "sources": [
        {"url": "https://sg.die-mitte.ch/menschen/", "status": "parsed", "matched_members": 2}]}
    annotate_directory_failures(current, previous)
    assert current["review_queue"] == []
    assert current["sources"][0]["last_success_at"] == current["checked_at"]
    assert current["sources"][0]["failure_streak"] == 0
