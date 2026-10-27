"""Scheduled check tests, without network calls."""
import asyncio
from datetime import datetime, timezone, timedelta
import httpx
import pytest
from app import admin
from app.main import app
from app.social_check_scheduler import interval_hours, due, run_due
from app.social_check_history import save_report


def test_schedule_disabled_by_default(monkeypatch):
    monkeypatch.delenv('SOCIAL_CHECK_INTERVAL_HOURS', raising=False)
    assert interval_hours() == 0
    assert asyncio.run(run_due()) is None


def test_schedule_validates_interval(monkeypatch):
    monkeypatch.setenv('SOCIAL_CHECK_INTERVAL_HOURS', '0.5')
    with pytest.raises(ValueError):
        interval_hours()
    monkeypatch.setenv('SOCIAL_CHECK_INTERVAL_HOURS', '24')
    assert interval_hours() == 24


def test_due_time():
    now = datetime.now(timezone.utc)
    assert due(None, 24)
    assert not due(now.isoformat(), 24)
    assert due((now - timedelta(hours=25)).isoformat(), 24)


def test_schedule_runs_only_when_due(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('SOCIAL_CHECK_INTERVAL_HOURS', '24')
    monkeypatch.setenv('SOCIAL_CHECK_LANGUAGES', 'DE')
    admin._live_task = None
    admin._live_job.clear()
    admin._live_job['state'] = 'idle'
    calls = []
    async def fake_check(lang):
        calls.append(lang)
        save_report(lang, {'checked_at': datetime.now(timezone.utc).isoformat(), 'results': [], 'checked': 0})
        admin._live_job['state'] = 'completed'
    monkeypatch.setattr(admin, '_execute_live_check', fake_check)
    asyncio.run(run_due())
    asyncio.run(run_due())
    assert calls == ['DE']


def test_schedule_status_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('SOCIAL_CHECK_INTERVAL_HOURS', '0')
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            assert (await client.get('/admin/live-check/schedule')).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            data = (await client.get('/admin/live-check/schedule')).json()
            assert data['enabled'] is False
    asyncio.run(run())
