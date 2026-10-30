from app.social_candidate_merge import merge_review_candidates
from app.social_cross_source import annotate_cross_source


def test_same_profile_preserves_two_independent_sources():
    queue = {"12": [{"url": "https://x.com/person", "source_url": "https://party.example/member",
                      "source_type": "official_party_profile", "review_status": "approved"}]}
    incoming = {"12": [{"url": "https://x.com/person/", "source_url": "https://person.example/about",
                        "source_type": "parliament_listed_personal_homepage"}]}
    merge_review_candidates(queue, incoming)
    annotate_cross_source(queue)
    assert len(queue["12"]) == 1
    assert queue["12"][0]["independent_source_count"] == 2
    assert queue["12"][0]["review_status"] == "approved"
    assert queue["12"][0]["auto_accept"] is False


def test_repeat_discovery_is_idempotent():
    queue = {}
    source = {"7": [{"url": "https://instagram.com/person", "source_url": "https://party.example/p",
                     "source_type": "official_party_profile"}]}
    merge_review_candidates(queue, source)
    merge_review_candidates(queue, source)
    assert len(queue["7"]) == 1
    assert len(queue["7"][0]["source_observations"]) == 1
