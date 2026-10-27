"""Consistent server-side alert filtering and CSV parity."""
import asyncio
import csv
import io
import httpx
import pytest
from app import admin
from app.main import app
from app.social_alert_filters import filter_alerts
from app.social_check_history import save_report
from app.social_csv_export import export_alerts


def report():
    return {'checked_at': '2026-10-27T10:00:00+00:00', 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': 'https://instagram.com/example',
         'status': 'possibly_missing', 'http_status': 404},
        {'person_number': '43', 'platform': 'Facebook', 'url': 'https://facebook.com/example',
         'status': 'restricted_or_rate_limited', 'http_status': 403}]}


def test_filters_search_platform_state_severity_and_date():
    items = [{'person_number': '42', 'platform': 'Instagram', 'url': 'https://instagram.com/example',
              'severity': 'high', 'review_state': 'open', 'review_reason': 'Bitte prüfen', 'new_problem': True},
             {'person_number': '43', 'platform': 'Facebook', 'url': 'https://facebook.com/example',
              'severity': 'low', 'review_state': 'ignored', 'review_reason': '', 'new_problem': False}]
    def select(**kwargs):
        return filter_alerts(items, checked_at='2026-10-27T10:00:00+00:00', **kwargs)
    for query in ({'search': '42'}, {'search': 'prüfen'}, {'search': 'INSTAGRAM'},
                  {'platform': 'instagram'}, {'severity': 'high'}, {'state': 'open'},
                  {'new_only': True}, {'date_from': '2026-10-27', 'date_to': '2026-10-27'}):
        expected = items if 'date_from' in query else items[:1]
        assert select(**query) == expected
    assert select(date_from='2026-10-28') == []
    assert select(date_to='2026-10-26') == []
    assert select(search='not-found') == []
    for query in ({'severity': 'invalid'}, {'state': 'invalid'}, {'date_from': 'yesterday'},
                  {'date_from': '2026-10-28', 'date_to': '2026-10-27'}, {'search': 'x' * 201}):
        with pytest.raises(ValueError):
            select(**query)


def test_export_and_api_filter_parity(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    save_report('DE', report())
    csv_data = export_alerts('DE', platform='Instagram', severity='high')
    rows = list(csv.DictReader(io.StringIO(csv_data.lstrip('\ufeff')), delimiter=';'))
    assert len(rows) == 1 and rows[0]['person_number'] == '42'
    assert len(list(csv.DictReader(io.StringIO(export_alerts('DE', date_to='2026-10-26').lstrip('\ufeff')), delimiter=';'))) == 0
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            params = {'language': 'DE', 'platform': 'Instagram', 'severity': 'high'}
            assert (await client.get('/admin/live-check/alerts', params=params)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            data = (await client.get('/admin/live-check/alerts', params=params)).json()
            assert data['filtered_count'] == 1 and data['alerts'][0]['person_number'] == rows[0]['person_number']
            response = await client.get('/admin/live-check/alerts/export.csv', params=params)
            assert response.status_code == 200
            assert len(list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff')), delimiter=';'))) == 1
            for invalid in ({'severity': 'critical'}, {'date_from': 'bad'},
                            {'date_from': '2026-10-28', 'date_to': '2026-10-27'}):
                assert (await client.get('/admin/live-check/alerts', params=invalid)).status_code == 422
                assert (await client.get('/admin/live-check/alerts/export.csv', params=invalid)).status_code == 422
    asyncio.run(run())
