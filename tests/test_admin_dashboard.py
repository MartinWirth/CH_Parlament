"""Authenticated admin dashboard integration tests using FastAPI ASGI transport."""
import asyncio

import httpx
import pytest

from app.main import app
from app import admin


@pytest.fixture(autouse=True)
def reset_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_REFRESH_PASSWORD", "test-admin-password")
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "test-refresh-token")
    admin._sessions.clear()
    admin._job.clear()
    admin._job.update(state="idle", started_at=None, finished_at=None, error=None)
    admin._task = None
    yield
    admin._sessions.clear()


def test_login_requires_origin_and_password():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            r = await client.post("/admin/login", json={"password": "test-admin-password"})
            assert r.status_code == 403
            r = await client.post("/admin/login", json={"password": "wrong"},
                                  headers={"Origin": "https://example.test", "X-Admin-Action": "1"})
            assert r.status_code == 403
            r = await client.post("/admin/login", json={"password": "test-admin-password"},
                                  headers={"Origin": "https://example.test", "X-Admin-Action": "1"})
            assert r.status_code == 200
            assert "httponly" in r.headers["set-cookie"].lower()
            assert "secure" in r.headers["set-cookie"].lower()
            assert "samesite=strict" in r.headers["set-cookie"].lower()
            assert (await client.get("/admin/status")).status_code == 200
    asyncio.run(run())


def test_refresh_requires_auth_and_csrf_and_reports_completion(monkeypatch):
    from app import main
    calls = []
    async def fake_refresh(language, force):
        calls.append((language, force))
        return {"updated_at": "2026-10-27T00:00:00Z"}
    monkeypatch.setattr(main, "_run_social_profiles_refresh", fake_refresh)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            assert (await client.post("/admin/refresh", json={})).status_code == 401
            login = await client.post("/admin/login", json={"password": "test-admin-password"},
                                      headers={"Origin": "https://example.test", "X-Admin-Action": "1"})
            assert login.status_code == 200
            assert (await client.post("/admin/refresh", json={"language": "DE"})).status_code == 403
            headers = {"Origin": "https://example.test", "X-Admin-Action": "1"}
            bad = await client.post("/admin/refresh", json={"language": "INVALID"}, headers=headers)
            assert bad.status_code == 422
            start = await client.post("/admin/refresh", json={"language": "FR", "force": True}, headers=headers)
            assert start.status_code == 200
            await asyncio.wait_for(admin._task, timeout=3)
            status = (await client.get("/admin/status")).json()
            assert status["state"] == "completed"
            assert calls == [("FR", True)]
            logout = await client.post("/admin/logout", headers=headers)
            assert logout.status_code == 200
            assert (await client.get("/admin/status")).status_code == 401
    asyncio.run(run())


def test_admin_fails_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("ADMIN_REFRESH_PASSWORD")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            response = await client.post("/admin/login", json={"password": "anything"},
                                         headers={"Origin": "https://example.test", "X-Admin-Action": "1"})
            assert response.status_code == 503
    asyncio.run(run())
