"""Tests for non-destructive social profile quality audit."""
import asyncio
import httpx

from app.main import app, verified_social_url
from app.social_quality import audit_profiles, canonical_url
from app import admin, main


def test_audit_detects_invalid_and_cross_member_duplicates_without_changes():
    profiles = {
        "1": {"Instagram": "https://www.instagram.com/one/"},
        "2": {"Instagram": "https://www.instagram.com/one/?utm_source=test"},
        "3": {"Instagram": "http://instagram.com/unsafe"},
        "4": {"LinkedIn": "https://www.instagram.com/wrong"},
    }
    before = {n: dict(links) for n, links in profiles.items()}
    report = audit_profiles(profiles, verified_social_url)
    assert report["checked"] == 4
    assert report["duplicates"] == 2
    assert report["invalid"] == 2
    assert profiles == before


def test_canonical_url_drops_tracking_and_rejects_credentials():
    assert canonical_url("https://x.com/person/?ref=abc") == "https://x.com/person"
    assert canonical_url("https://user:password@x.com/person") is None
    assert canonical_url("http://x.com/person") is None


def test_quality_endpoint_requires_admin_and_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_REFRESH_PASSWORD", "secret")
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "token")
    monkeypatch.setattr(main, "_profile_snapshot_path", tmp_path / "snapshot.json")
    main.save_profile_snapshot({"DE": {"profiles": {"1": {"Instagram": "https://instagram.com/example"}}}})
    admin._sessions.clear()
    headers = {"Origin": "https://example.test", "X-Admin-Action": "1"}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            assert (await client.get("/admin/quality")).status_code == 401
            login = await client.post("/admin/login", json={"password": "secret"}, headers=headers)
            assert login.status_code == 200
            response = await client.get("/admin/quality?language=DE")
            assert response.status_code == 200
            assert response.json()["checked"] == 1
            assert (await client.get("/admin/quality?language=XX")).status_code == 422
    asyncio.run(run())
    assert main.read_profile_snapshot()["DE"]["profiles"]["1"]["Instagram"] == "https://instagram.com/example"


def test_quality_flags_content_links_for_review_without_deleting():
    profiles = {
        "1": {"LinkedIn": "https://www.linkedin.com/company/example"},
        "2": {"Instagram": "https://instagram.com/p/example"},
        "3": {"X": "https://x.com/politician"},
    }
    before = {key: dict(value) for key, value in profiles.items()}
    report = audit_profiles(profiles, verified_social_url)
    assert report["needs_review"] == 2
    assert report["invalid"] == 0
    assert profiles == before


def test_quality_detects_www_mobile_duplicates():
    profiles = {
        "1": {"Facebook": "https://www.facebook.com/profile.php?id=123&utm_source=abc"},
        "2": {"Facebook": "https://m.facebook.com/profile.php?id=123"},
    }
    report = audit_profiles(profiles, verified_social_url)
    assert report["duplicates"] == 2
    assert canonical_url("https://m.facebook.com/profile.php?id=123") == "https://www.facebook.com/profile.php?id=123"
