"""Ensure curated regional party sources remain identity-bound and on official hosts."""
from pathlib import Path
from app.social_regional_discovery import configured_regional_pages


def test_seeded_regional_party_sources():
    names = {
        "2585": "Martin Bäumle",
        "11507": "Patrick Hässig",
        "2700": "Tiana Angelina Moser",
        "3179": "Barbara Schaffner",
    }
    path = Path(__file__).resolve().parents[1] / "social_regional_party_sources.json"
    pages, diagnostic = configured_regional_pages(names, path)
    assert len(pages) == 4
    assert diagnostic["accepted"] == 4
    assert diagnostic["rejected"] == 0
    assert all(url.startswith("https://zh.glp.swiss/personen/") for url in pages.values())


def test_seeded_sources_require_current_member_identity():
    path = Path(__file__).resolve().parents[1] / "social_regional_party_sources.json"
    pages, diagnostic = configured_regional_pages({"11507": "Someone Else"}, path)
    assert pages == {}
    assert diagnostic["rejected"] == 4
