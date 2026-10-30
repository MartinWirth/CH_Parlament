from app.social_discovery_report import discovery_report


def test_discovery_report_prioritizes_uncovered_members_and_preserves_sources():
    snapshot = {
        "updated_at": "2026-10-30",
        "profiles": {"1": {"instagram": "https://instagram.com/known"}},
        "review_candidates": {
            "1": [{"url": "https://instagram.com/a", "platform": "instagram",
                   "source_type": "official_party_profile", "source_url": "https://party.example/a",
                   "review_priority_score": 60}],
            "2": [{"url": "https://instagram.com/b", "platform": "instagram",
                   "source_type": "external_candidate", "source_url": "https://party.example/b",
                   "review_priority_score": 30, "independent_source_count": 2},
                  {"url": "https://instagram.com/b", "platform": "instagram",
                   "source_type": "external_candidate", "source_url": "https://party.example/b2"}],
        },
    }
    report = discovery_report(snapshot)
    assert report["confirmed_member_count"] == 1
    assert report["total_distinct_candidates"] == 2
    assert report["members_without_confirmed_profiles_with_candidates"] == 1
    assert report["candidates"][0]["person_number"] == "2"
    assert report["candidates"][0]["requires_identity_verification"] is True
    assert report["candidates"][0]["auto_accept"] is False


def test_discovery_report_handles_empty_snapshot():
    report = discovery_report({})
    assert report["total_distinct_candidates"] == 0
    assert report["candidates"] == []
