from app.social_cross_source import annotate_cross_source


def test_independent_sources_and_duplicate_source_pages():
    queue = {"1": [
        {"url": "https://www.instagram.com/example/", "source_url": "https://party.example/a"},
        {"url": "https://www.instagram.com/example", "source_url": "https://party.example/b"},
        {"url": "https://www.instagram.com/example", "source_url": "https://party.example/b"},
    ]}
    annotate_cross_source(queue)
    assert all(item["independent_source_count"] == 2 for item in queue["1"])
    assert all(item["requires_identity_verification"] for item in queue["1"])


def test_collision_with_other_member_and_existing_profile():
    queue = {"1": [{"url": "https://x.com/example", "source_url": "https://party.example/a"}]}
    profiles = {"2": {"X": "https://x.com/example/"}}
    annotate_cross_source(queue, profiles)
    assert queue["1"][0]["identity_conflict_person_numbers"] == ["2"]
    assert "cross_member_identity_conflict" in queue["1"][0]["review_priority_reasons"]
    assert profiles["2"]["X"] == "https://x.com/example/"


def test_invalid_candidates_are_not_attributed():
    queue = {"1": [{"url": "http://x.com/example", "source_url": "https://example.org"}]}
    annotate_cross_source(queue)
    assert queue["1"][0]["independent_source_count"] == 0
    assert queue["1"][0]["identity_conflict_person_numbers"] == []
