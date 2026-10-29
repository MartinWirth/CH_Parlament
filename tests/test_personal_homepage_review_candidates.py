"""Unverified social links from parliament-listed personal sites remain review-only."""
from app.social_evidence import discover_personal_homepage_review_candidates
from app.main import verified_social_url


def test_trusted_homepage_candidates_and_deduplication():
    source = "https://example-politician.ch/"
    report = {"missing_details": [{
        "person_number": "101", "name": "Ada Muster", "homepage": source,
        "unverified_personal_candidates": [
            {"url": "https://www.instagram.com/ada_public", "source_page": source,
             "status": "requires_identity_verification", "classification_reason": "uncertain"},
            {"url": "https://www.instagram.com/ada_public", "source_page": source,
             "status": "requires_identity_verification"},
            {"url": "https://www.linkedin.com/in/ada", "source_page": "https://example-politician.ch/social",
             "status": "requires_identity_verification"},
            {"url": "https://www.facebook.com/party", "source_page": "https://attacker.example/",
             "status": "requires_identity_verification"},
            {"url": "https://www.instagram.com/party", "source_page": source,
             "status": "organization"},
        ],
    }]}
    data = discover_personal_homepage_review_candidates(
        report, {"101": "Ada Muster"}, {"101": source}, verified_social_url,
        "2026-10-29T00:00:00+00:00")
    assert len(data["101"]) == 2
    assert {item["platform"] for item in data["101"]} == {"Instagram", "LinkedIn"}
    assert all(item["auto_accept"] is False for item in data["101"])
    assert all(item["verification_status"] == "requires_identity_verification" for item in data["101"])
    assert data["101"][0]["source_url"] == source


def test_reject_wrong_identity_homepage_and_host():
    report = {"missing_details": [{
        "person_number": "101", "name": "Wrong Name", "homepage": "https://example.ch",
        "unverified_personal_candidates": [{
            "url": "https://instagram.com/possible", "source_page": "https://example.ch",
            "status": "requires_identity_verification"}]}]}
    assert discover_personal_homepage_review_candidates(
        report, {"101": "Ada Muster"}, {"101": "https://example.ch"},
        verified_social_url) == {}
    report["missing_details"][0]["name"] = "Ada Muster"
    assert discover_personal_homepage_review_candidates(
        report, {"101": "Ada Muster"}, {"101": "https://other.ch"},
        verified_social_url) == {}
