from app.social_targeted_scan import _safe_source, discover_uncovered_homepage_candidates
import asyncio


def test_targeted_source_validation():
    assert _safe_source("https://example.org/person")
    for url in ("http://example.org", "https://user@example.org/",
                "https://example.org:8443/", "https://example.org/#fragment",
                "file:///etc/passwd", None):
        assert not _safe_source(url)


def test_targeted_scan_does_not_fetch_without_eligible_sources():
    found, diagnostic = asyncio.run(discover_uncovered_homepage_candidates(
        {"1": "Example"}, {"1": "http://unsafe.invalid"},
        {}, lambda url: "LinkedIn", checked_at="2026-10-30T00:00:00Z"))
    assert found == {}
    assert diagnostic["eligible_scanned"] == 0


def test_targeted_scan_skips_confirmed_members():
    found, diagnostic = await discover_uncovered_homepage_candidates(
        {"1": "Example"}, {"1": "https://example.org/person"},
        {"1": {"LinkedIn": "https://linkedin.com/in/example"}},
        lambda url: "LinkedIn", checked_at="2026-10-30T00:00:00Z"))
    assert found == {}
    assert diagnostic["eligible_scanned"] == 0
