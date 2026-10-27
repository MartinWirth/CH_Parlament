"""Manual triage persistence, validation, and access controls."""
import asyncio
import httpx
import pytest
from app import admin
from app.main import app
from app.social_alert_triage import save_decision, decisions
from app.social_check_history import save_report
from app.social_alerts import latest_alerts


def sample():
    return {'checked_at': '2026-10-27T10:00:00+00:00', 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': 'https://instagram.com/member',
         'status': 'possibly_missing', 'http_status': 404}]}


def test_persists_and_attaches_decisions(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('DE', sample())
    assert latest_alerts('DE')['alerts'][0]['review_state'] == 'open'
    save_decision('DE', '42', 'Instagram', 'https://instagram.com/member/', 'in_progress', 'Investigating')
    alert = latest_alerts('DE')['alerts'][0]
    assert alert['review_state'] == 'in_progress'
    assert alert['review_reason'] == 'Investigating'
    assert decisions('FR') == {}
    save_decision('DE', '42', 'Instagram', 'https://instagram.com/member', 'resolved', 'Confirmed active manually')
    assert latest_alerts('DE')['alerts'][0]['review_state'] == 'resolved'


def test_rejects_invalid_or_unexplained_decisions(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    with pytest.raises(ValueError):
        save_decision('DE', '42', 'Instagram', 'https://instagram.com/member', 'ignored', '')
    with pytest.raises(ValueError):
        save_decision('DE', '42', 'Instagram', 'https://instagram.com/member', 'wrong', 'test')
    with pytest.raises(ValueError):
        save_decision('DE', '42', 'Instagram', 'http://localhost', 'open', '')


def test_triage_requires_auth_origin_and_current_alert(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    save_report('DE', sample())
    payload = {'language': 'DE', 'person_number': '42', 'platform': 'Instagram',
               'url': 'https://instagram.com/member', 'state': 'ignored', 'reason': 'Platform blocks bots'}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            path = '/admin/live-check/alerts/triage'
            assert (await client.post(path, json=payload)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            assert (await client.post(path, json=payload)).status_code == 403
            assert (await client.post(path, json=payload, headers=headers)).status_code == 200
            invalid = dict(payload, person_number='999')
            assert (await client.post(path, json=invalid, headers=headers)).status_code == 404
    asyncio.run(run())
