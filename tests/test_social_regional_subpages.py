import asyncio

from app.social_regional_discovery import discover_regional_party_candidates


def test_curated_person_page_scans_only_same_host_subpages(monkeypatch):
    import app.social_regional_discovery as discovery
    from app.main import verified_social_url

    monkeypatch.setattr(discovery, "configured_regional_pages",
                        lambda names, path, *, existing_profiles=None:
                        ({"1": "https://zh.glp.swiss/person/ada"}, {"accepted": 1}))
    scanned = []

    async def scan(client, url, semaphore, diagnostic):
        scanned.append(url)
        diagnostic["status"] = "parsed"
        if url.endswith("/person/ada"):
            diagnostic["internal_page_candidates"] = [
                "https://zh.glp.swiss/person/ada/contact",
                "https://evil.example/other",
                "https://zh.glp.swiss/person/ada/about",
            ]
            return {}
        diagnostic["social_url_candidates"] = ["https://instagram.com/ada"]
        return {}

    monkeypatch.setattr("app.main.scan_homepage", scan)
    found, diagnostic = asyncio.run(discover_regional_party_candidates(
        {"1": "Ada Muster"}, verified_social_url))
    assert scanned == ["https://zh.glp.swiss/person/ada",
                       "https://zh.glp.swiss/person/ada/contact",
                       "https://zh.glp.swiss/person/ada/about"]
    assert diagnostic["subpages_scanned"] == 2
    assert found["1"][0]["source_url"] == "https://zh.glp.swiss/person/ada/contact"
    assert found["1"][0]["auto_accept"] is False
    assert found["1"][0]["verification_status"] == "requires_identity_verification"
