import json
import asyncio
import pytest
from app.social_regional_discovery import configured_regional_pages, discover_regional_party_candidates


def test_only_exact_named_curated_official_party_pages(tmp_path):
    path = tmp_path / "pages.json"
    path.write_text(json.dumps([
        {"person_number": "1", "name": "Ada Muster", "profile_url": "https://zh.glp.swiss/personen/ada", "verified": True},
        {"person_number": "2", "name": "Bea Beispiel", "profile_url": "https://evilglp.swiss/personen/bea", "verified": True},
        {"person_number": "3", "name": "Wrong Name", "profile_url": "https://www.svp.ch/menschen/portrait/person", "verified": True},
        {"person_number": "4", "name": "Dora Test", "profile_url": "https://www.fdp.ch/personen/dora", "verified": False},
    ]))
    pages, stats = configured_regional_pages(
        {"1": "Ada Muster", "2": "Bea Beispiel", "3": "Cora Beispiel", "4": "Dora Test"}, path)
    assert pages == {"1": "https://zh.glp.swiss/personen/ada"}
    assert stats["accepted"] == 1 and stats["rejected"] == 3


def test_regional_links_are_review_only(monkeypatch):
    import app.social_regional_discovery as discovery
    from app.main import verified_social_url
    monkeypatch.setattr(discovery, "configured_regional_pages",
                        lambda names, path, *, existing_profiles=None: ({"1": "https://zh.glp.swiss/personen/ada"}, {"accepted": 1}))
    async def scan(client, url, semaphore, diagnostic):
        diagnostic["status"] = "parsed"
        diagnostic["social_url_candidates"] = ["https://instagram.com/ada",
                                                  "https://instagram.com/ada",
                                                  "https://example.org/not-social"]
        return {}
    monkeypatch.setattr("app.main.scan_homepage", scan)
    data, stats = asyncio.run(discover_regional_party_candidates(
        {"1": "Ada Muster"}, verified_social_url))
    assert len(data["1"]) == 1
    assert data["1"][0]["auto_accept"] is False
    assert data["1"][0]["verification_status"] == "requires_identity_verification"
    assert stats["candidates"] == 1
