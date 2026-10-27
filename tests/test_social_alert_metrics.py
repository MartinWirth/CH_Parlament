"""Metrics for current triage and chronological persisted check results."""
import asyncio
import httpx
import pytest
from app import admin
from app.main import app
from app.social_check_history import save_report
from app.social_alert_triage import save_decision
from app.social_alert_metrics import alert_metrics

URL = 'https://instagram.com/member'


def report(status, code, checked_at):
    return {'checked_at': checked_at, 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': URL,
         'status': status, 'http_status': code}]}


def test_empty_and_validation(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'checks.sqlite3'))
    result = alert_metrics('DE')
    assert result['summary']['active'] == 0
    assert result['trend'] == []
    for language, limit in [('XX', 10), ('DE', 0), ('DE', 31), ('DE', True)]:
        with pytest.raises(ValueError):
            alert_metrics(language, limit)


def test_trend_and_current_triage_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'checks.sqlite3'))
    save_report('DE', report('reachable', 200, '2026-10-26'))
    save_report('DE', report('possibly_missing', 404, '2026-10-27'))
    save_decision('DE', '42', 'Instagram', URL, 'in_progress', 'Investigating')
    result = alert_metrics('DE')
    assert result['summary']['active'] == 1
    assert result['summary']['high_active'] == 1
    assert result['summary']['states']['in_progress'] == 1
    assert [item['flagged'] for item in result['trend']] == [0, 1]
    assert result['trend'][1]['high'] == 1
    save_decision('DE', '42', 'Instagram', URL, 'resolved', 'Verified')
    result = alert_metrics('DE', limit=1)
    assert result['summary']['active'] == 0
    assert result['summary']['states']['resolved'] == 1
    assert len(result['trend']) == 1
    assert result['trend'][0]['flagged'] == 1
    assert alert_metrics('FR')['trend'] == []


def test_metrics_endpoint_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'checks.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            path = '/admin/live-check/metrics'
            assert (await client.get(path)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            assert (await client.get(path, params={'language': 'DE'})).status_code == 200
            assert (await client.get(path, params={'language': 'XX'})).status_code == 422
            assert (await client.get(path, params={'limit': 100})).status_code == 422
    asyncio.run(run())
