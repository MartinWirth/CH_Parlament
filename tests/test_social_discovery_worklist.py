"""Research queue does not fabricate personal social account ownership."""
from urllib.parse import parse_qs, urlparse
import pytest
from app import social_discovery_worklist as worklist


def test_missing_platforms_and_pending_queue(monkeypatch):
    from app import main
    snapshot = {"DE": {
        "member_names": {"1": "Ada Muster", "2": "Bea Beispiel"},
        "profiles": {"1": {"Instagram": "https://www.instagram.com/adamuster"}},
        "review_candidates": {"1": [{"platform": "LinkedIn", "url": "https://linkedin.com/in/ada",
                                     "verification_status": "requires_identity_verification"}]},
    }}
    monkeypatch.setattr(main, "read_profile_snapshot", lambda: snapshot)
    monkeypatch.setattr("app.social_review.apply_decisions", lambda *args: None)
    data = worklist.discovery_worklist("DE")
    assert data["total"] == 8
    assert not any(item["person_number"] == "1" and item["platform"] in ("Instagram", "LinkedIn")
                   for item in data["items"])
    assert all(item["requires_manual_verification"] for item in data["items"])
    query = parse_qs(urlparse(data["items"][0]["search_url"]).query)["q"][0]
    assert "site:" in query and "Schweiz" in query
    assert worklist.discovery_worklist("DE", platform="X")["total"] == 2
    assert len(worklist.discovery_worklist("DE", limit=1)["items"]) == 1


def test_invalid_query(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "read_profile_snapshot", lambda: {})
    for kwargs in ({"language": "XX"}, {"language": "DE", "limit": 0},
                   {"language": "DE", "limit": True},
                   {"language": "DE", "platform": "TikTok"}):
        with pytest.raises(ValueError):
            worklist.discovery_worklist(**kwargs)
