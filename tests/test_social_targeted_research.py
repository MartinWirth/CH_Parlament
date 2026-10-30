from app.social_targeted_research import targeted_research_queue


def test_uncovered_research_priorities_and_no_automatic_acceptance():
    snapshot = {
        "member_names": {"1": "Anna Muster", "2": "Berta Beispiel", "3": "Carlo Test", "4": "Dora Test"},
        "profiles": {"4": {"LinkedIn": "https://www.linkedin.com/in/dora"}},
        "homepage_urls": {"2": "https://example.org/berta"},
        "review_candidates": {"1": [{"url": "https://linkedin.com/in/anna"}]},
    }
    result = targeted_research_queue(snapshot)
    assert result["total_uncovered"] == 3
    assert [row["person_number"] for row in result["worklist"]] == ["1", "2", "3"]
    assert [row["priority"] for row in result["worklist"]] == [
        "review_existing", "inspect_homepage", "find_official_source"]
    assert all(row["auto_accept"] is False and row["requires_identity_verification"]
               for row in result["worklist"])


def test_targeted_research_limits_and_invalid_snapshot_fields():
    result = targeted_research_queue({"member_names": {"1": "A", "2": "B"},
                                      "profiles": None, "review_candidates": []}, limit=1)
    assert result["total_uncovered"] == 2
    assert result["truncated"] is True
    assert len(result["worklist"]) == 1
