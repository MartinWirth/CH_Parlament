"""Automatic recurrence handling with persistent manual/automatic audit history."""
import asyncio
import httpx
from app import admin
from app.main import app
from app.social_check_history import save_report
from app.social_alert_triage import save_decision, decisions, events

URL = 'https://instagram.com/member'


def report(status, code):
    return {'checked_at': '2026-10-27T10:00:00+00:00', 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': URL,
         'status': status, 'http_status': code}]}


def test_reopens_only_after_reachable_to_problem(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('DE', report('possibly_missing', 404))
    save_decision('DE', '42', 'Instagram', URL, 'resolved', 'Checked manually')
    save_report('DE', report('possibly_missing', 404))
    assert decisions('DE')[('42', 'Instagram', URL)]['state'] == 'resolved'
    save_report('DE', report('reachable', 200))
    assert decisions('DE')[('42', 'Instagram', URL)]['state'] == 'resolved'
    save_report('DE', report('possibly_missing', 404))
    state = decisions('DE')[('42', 'Instagram', URL)]
    assert state['state'] == 'open'
    history = events('DE', '42', 'Instagram', URL)
    assert len(history) == 2
    assert history[0]['reason'] == 'Checked manually'
    assert history[0]['source'] == 'manual'
    assert history[1]['source'] == 'automatic'
    assert history[1]['old_state'] == 'resolved'
    assert history[1]['new_state'] == 'open'
    save_report('DE', report('possibly_missing', 404))
    assert len(events('DE', '42', 'Instagram', URL)) == 2


def test_ignored_reopens_but_in_progress_does_not(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('FR', report('reachable', 200))
    save_decision('FR', '42', 'Instagram', URL, 'ignored', 'Temporary bot block')
    save_report('FR', report('redirect', 301))
    assert decisions('FR')[('42', 'Instagram', URL)]['state'] == 'open'
    save_report('FR', report('reachable', 200))
    save_decision('FR', '42', 'Instagram', URL, 'in_progress', 'Working on it')
    save_report('FR', report('redirect', 301))
    assert decisions('FR')[('42', 'Instagram', URL)]['state'] == 'in_progress'
    assert decisions('DE') == {}


def test_audit_endpoint_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    save_decision('DE', '42', 'Instagram', URL, 'resolved', 'Checked')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            path = '/admin/live-check/alerts/events'
            params = {'language': 'DE', 'person_number': '42', 'platform': 'Instagram', 'url': URL}
            assert (await client.get(path, params=params)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            response = await client.get(path, params=params)
            assert response.status_code == 200
            assert response.json()['events'][0]['source'] == 'manual'
    asyncio.run(run())
