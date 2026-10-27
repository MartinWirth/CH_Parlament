"""Durable social check history and authenticated history endpoint."""
import asyncio

import httpx

from app import admin
from app.main import app
from app.social_check_history import compare_reports, get_history, save_report


def report(status, http_status=200, url="https://instagram.com/member"):
    return {"checked_at": "2026-10-27T10:00:00+00:00", "checked": 1,
            "results": [{"person_number": "42", "platform": "Instagram",
                         "url": url, "status": status, "http_status": http_status}]}


def test_history_persists_changes_and_flags(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "history.sqlite3"
    monkeypatch.setenv("SOCIAL_CHECK_HISTORY_PATH", str(path))
    save_report("DE", report("reachable", 200))
    save_report("DE", report("possibly_missing", 404))
    assert path.exists()
    runs = get_history("DE")
    assert len(runs) == 2
    assert runs[0]["flagged"] == 1
    assert runs[0]["comparison_available"]
    assert runs[0]["changes"][0]["previous_status"] == "reachable"
    assert runs[0]["changes"][0]["status"] == "possibly_missing"
    assert runs[1]["changes"] == []


def test_history_is_language_isolated_and_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_CHECK_HISTORY_PATH", str(tmp_path / "checks.sqlite3"))
    for index in range(35):
        save_report("DE", report("reachable", 200))
    save_report("FR", report("redirect", 301))
    assert len(get_history("DE", limit=30)) == 30
    assert len(get_history("FR")) == 1
    assert len(get_history("DE")) == 10


def test_comparison_never_infers_unchecked_links_missing():
    previous = report("reachable")
    current = {"results": []}
    assert compare_reports(previous, current) == []
    assert compare_reports(previous, report("reachable", 200, url="https://instagram.com/other")) == []


def test_history_requires_admin_and_valid_language(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_CHECK_HISTORY_PATH", str(tmp_path / "checks.sqlite3"))
    monkeypatch.setenv("ADMIN_REFRESH_PASSWORD", "secret")
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "token")
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            assert (await client.get("/admin/live-check/history")).status_code == 401
            headers = {"Origin": "https://example.test", "X-Admin-Action": "1"}
            login = await client.post("/admin/login", json={"password": "secret"}, headers=headers)
            assert login.status_code == 200
            assert (await client.get("/admin/live-check/history?language=XX")).status_code == 422
            result = await client.get("/admin/live-check/history?language=DE")
            assert result.status_code == 200
            assert result.json()["runs"] == []
    asyncio.run(run())
