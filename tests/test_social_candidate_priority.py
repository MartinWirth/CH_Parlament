"""Tests for explainable, non-destructive candidate ranking."""
from app.social_candidate_priority import annotate_candidates


def test_priority_prefers_personal_homepage_evidence():
    queue = {"42": [
        {"platform": "LinkedIn", "url": "https://linkedin.com/in/example",
         "source_type": "external_candidate", "source_url": "https://example.org/source"},
        {"platform": "LinkedIn", "url": "https://linkedin.com/in/another",
         "source_type": "parliament_listed_personal_homepage",
         "source_url": "https://example.org/personal"},
    ]}
    annotate_candidates(queue)
    assert queue["42"][1]["review_priority_score"] > queue["42"][0]["review_priority_score"]
    assert all("review_priority_reasons" in item for item in queue["42"])
    assert all("review_status" not in item for item in queue["42"])


def test_non_personal_content_gets_review_warning_without_removal():
    candidate = {"platform": "Instagram", "url": "https://instagram.com/p/post123",
                 "source_type": "official_party_profile",
                 "source_url": "https://party.example/member"}
    queue = {"42": [candidate]}
    annotate_candidates(queue)
    assert candidate["review_priority_score"] == 20
    assert "instagram_content_not_profile" in candidate["review_priority_reasons"]
    assert queue["42"][0]["url"] == "https://instagram.com/p/post123"


def test_invalid_urls_are_low_priority():
    queue = {"42": [{"platform": "X", "url": "http://x.com/example",
                      "source_type": "official_party_profile"}]}
    annotate_candidates(queue)
    assert queue["42"][0]["review_priority_score"] == 0
