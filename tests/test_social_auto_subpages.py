import asyncio

from app.social_regional_discovery import discover_regional_party_candidates


def test_automatic_subpages_deduplicate_and_prioritize_uncovered(monkeypatch):
    import app.social_regional_discovery as discovery

    monkeypatch.setattr(discovery, "configured_regional_pages",
                        lambda names, path, *, existing_profiles=None:
                        ({"1": "https://zh.glp.swiss/person/one",
                          "2": "https://zh.glp.swiss/person/two"}, {"accepted": 2}))
    monkeypatch.setattr(discovery, "configured_additional_party_pages",
                        lambda names, path, *, existing_profiles=None:
                        [("1", "https://zh.glp.swiss/person/one/contact")])
    scanned = []

    async def fake_scan(client, url, semaphore, report):
        scanned.append(url)
        report["status"] = "parsed"
        if url.endswith("/person/one"):
            report["internal_page_candidates"] = [
                "https://zh.glp.swiss/person/one/contact",
                "https://zh.glp.swiss/person/one/about",
                "https://zh.glp.swiss/person/one/team",
                "https://other.example/unsafe"]
        if url.endswith("/person/two"):
            report["internal_page_candidates"] = [
                "https://zh.glp.swiss/person/two/contact"]
        return {}

    monkeypatch.setattr("app.main.scan_homepage", fake_scan)
    _, diagnostics = asyncio.run(discover_regional_party_candidates(
        {"1": "One", "2": "Two"}, lambda url: None,
        existing_profiles={"2": {"LinkedIn": "confirmed"}}))
    assert scanned.count("https://zh.glp.swiss/person/one/contact") == 1
    assert "https://zh.glp.swiss/person/one/about" in scanned
    assert "https://zh.glp.swiss/person/one/team" not in scanned
    assert diagnostics["auto_discovered_subpages"] == 2
    assert diagnostics["subpages_scanned"] == 3
