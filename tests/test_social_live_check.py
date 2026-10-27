"""Live-check classification and security regression tests."""
import asyncio
from unittest.mock import patch
import httpx

from app import admin, main
from app.main import app
from app.social_live_check import check_links, classify_status


def test_status_classification():
    assert classify_status(200) == "reachable"
    assert classify_status(301) == "redirect"
    assert classify_status(404) == "possibly_missing"
    assert classify_status(403) == "restricted_or_rate_limited"
    assert classify_status(429) == "restricted_or_rate_limited"


def test_live_check_deduplicates_and_does_not_follow_redirects():
    calls = []
    class FakeClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(302, headers={"location": "/new"})
    async def run():
        return await check_links({"1": {"Instagram": "https://instagram.com/example"},
                                  "2": {"Instagram": "https://instagram.com/example/?track=1"}},
                                 lambda url: "Instagram", client_factory=FakeClient)
    report = asyncio.run(run())
    assert report["checked"] == 1
    assert len(calls) == 1
    assert report["results"][0]["status"] == "redirect"


def test_live_check_skips_private_and_non_https_urls():
    class NoNetwork:
        def __init__(self, **kwargs):
            raise AssertionError("Unexpected network access")
    report = asyncio.run(check_links(
        {"1": {"Instagram": "http://instagram.com/x"},
         "2": {"Instagram": "https://127.0.0.1/x"}},
        lambda url: None, client_factory=NoNetwork))
    assert report["checked"] == 0


def test_live_check_requires_admin_and_origin(monkeypatch):
    monkeypatch.setenv("ADMIN_REFRESH_PASSWORD", "secret")
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "token")
    admin._sessions.clear()
    headers = {"Origin": "https://example.test", "X-Admin-Action": "1"}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            assert (await client.get("/admin/live-check")).status_code == 401
            login = await client.post("/admin/login", json={"password": "secret"}, headers=headers)
            assert login.status_code == 200
            assert (await client.post("/admin/live-check", json={"language": "DE"})).status_code == 403
            assert (await client.post("/admin/live-check", json={"language": "XX"}, headers=headers)).status_code == 422
    asyncio.run(run())
