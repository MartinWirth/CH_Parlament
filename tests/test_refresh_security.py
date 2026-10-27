"""Regression tests for refresh HTTP semantics, token authorization and concurrency."""
import asyncio

import httpx
from app import main


def test_refresh_requires_post_and_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "secret-for-test")
    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/social-profiles/refresh")).status_code == 405
            assert (await client.post("/api/social-profiles/refresh", headers={"X-Refresh-Token": "secret-for-test"})).status_code == 403
            assert (await client.post("/api/social-profiles/refresh", headers={"X-Refresh-Token": "wrong"})).status_code == 403
    asyncio.run(run())


def test_refresh_serializes_concurrent_requests(monkeypatch):
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "secret-for-test")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_refresh(language, force):
        entered.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(main, "_run_social_profiles_refresh", fake_refresh)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/social-profiles/refresh"))
            await entered.wait()
            second = await client.post("/api/social-profiles/refresh", headers={"X-Refresh-Token": "secret-for-test"})
            assert second.status_code == 409
            release.set()
            response = await first
            assert response.status_code == 200
            assert response.json() == {"ok": True}
    asyncio.run(run())

def test_refresh_disabled_without_token(monkeypatch):
    monkeypatch.delenv("SOCIAL_REFRESH_TOKEN", raising=False)
    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/api/social-profiles/refresh")).status_code == 503
    asyncio.run(run())


def test_homepage_scanning_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_EXTERNAL_HOMEPAGE_SCAN", raising=False)
    async def run():
        diagnostic = {}
        class NoNetwork:
            async def get(self, *args, **kwargs):
                raise AssertionError("Network must not be called")
        result = await main.scan_homepage(NoNetwork(), "https://example.com", asyncio.Semaphore(1), diagnostic)
        assert result == {}
        assert diagnostic["status"] == "external_scan_disabled"
    asyncio.run(run())
