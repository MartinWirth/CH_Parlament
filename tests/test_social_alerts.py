"""Prioritized link alert tests."""
import asyncio
import httpx
from app import admin
from app.main import app
from app.social_check_history import save_report
from app.social_alerts import latest_alerts, severity


def report(status, code):
    return {'checked_at': '2026-10-27T10:00:00+00:00', 'checked': 1, 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': 'https://instagram.com/example',
         'status': status, 'http_status': code}]}


def test_priorities():
    assert severity({'status': 'possibly_missing'}) == 'high'
    assert severity({'status': 'redirect'}) == 'medium'
    assert severity({'status': 'request_failed'}) == 'medium'
    assert severity({'status': 'restricted_or_rate_limited'}) == 'low'
    assert severity({'status': 'reachable'}) is None


def test_alerts_new_problem_and_language_separation(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('DE', report('reachable', 200))
    assert latest_alerts('DE')['alerts'] == []
    save_report('DE', report('possibly_missing', 404))
    result = latest_alerts('DE')
    assert result['counts'] == {'high': 1, 'medium': 0, 'low': 0, 'new': 1}
    assert result['alerts'][0]['new_problem'] is True
    assert latest_alerts('FR')['alerts'] == []
    save_report('DE', report('possibly_missing', 404))
    assert latest_alerts('DE')['counts']['new'] == 0


def test_first_alert_not_assumed_new(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('IT', report('restricted_or_rate_limited', 403))
    assert latest_alerts('IT')['counts'] == {'high': 0, 'medium': 0, 'low': 1, 'new': 0}


def test_alerts_endpoint_requires_login(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            assert (await client.get('/admin/live-check/alerts')).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            assert (await client.get('/admin/live-check/alerts?language=XX')).status_code == 422
            assert (await client.get('/admin/live-check/alerts?language=DE')).status_code == 200
    asyncio.run(run())
