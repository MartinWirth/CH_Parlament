from app.social_directory_trends import annotate_directory_failures


def test_three_consecutive_failed_refreshes_trigger_warning():
    previous = {}
    for n in range(1, 4):
        current = {"sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                                "status": "error", "reason": "HTTP 404"}]}
        annotate_directory_failures(current, previous)
        item = current["sources"][0]
        assert item["failure_streak"] == n
        assert item["persistent_failure"] is (n >= 3)
        assert current["persistent_failure_count"] == (1 if n >= 3 else 0)
        previous = current


def test_success_resets_streak_and_zero_matches_not_failure():
    old = {"sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                        "status": "error", "failure_streak": 9}]}
    current = {"sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                           "status": "parsed", "matched_members": 0,
                           "reason": "no_matching_member_links"}]}
    annotate_directory_failures(current, old)
    assert current["sources"][0]["failure_streak"] == 0
    assert current["persistent_failure_count"] == 0


def test_new_url_does_not_inherit_old_streak():
    old = {"sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                        "status": "error", "failure_streak": 99}]}
    current = {"sources": [{"url": "https://tg.die-mitte.ch/menschen/",
                           "status": "error"}]}
    annotate_directory_failures(current, old)
    assert current["sources"][0]["failure_streak"] == 1
