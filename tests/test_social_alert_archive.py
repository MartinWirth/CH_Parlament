"""Historical archive snapshots, transitions and authenticated exports."""
import asyncio
import csv
import io
import httpx
import pytest
from app import admin
from app.main import app
from app.social_check_history import save_report
from app.social_alert_archive import archived_alerts
from app.social_csv_export import export_archive


def report(stamp, status, code):
    return {'checked_at': stamp, 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': 'https://instagram.com/example',
         'status': status, 'http_status': code}]}


def test_archive_preserves_observations_and_transitions(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('DE', report('2026-10-25T10:00:00+00:00', 'reachable', 200))
    save_report('DE', report('2026-10-26T10:00:00+00:00', 'possibly_missing', 404))
    save_report('DE', report('2026-10-27T10:00:00+00:00', 'redirect', 301))
    data = archived_alerts('DE')
    assert data['total'] == 2
    assert [item['status'] for item in data['alerts']] == ['redirect', 'possibly_missing']
    assert data['alerts'][1]['new_problem'] is True
    assert data['alerts'][0]['previous_status'] == 'possibly_missing'
    assert archived_alerts('DE', date_from='2026-10-27')['total'] == 1
    assert archived_alerts('DE', date_to='2026-10-26', new_only=True)['total'] == 1
    assert archived_alerts('DE', search='42', platform='instagram', severity='high')['total'] == 1
    assert archived_alerts('DE', limit=1)['total'] == 1
    assert archived_alerts('FR')['total'] == 0
    exported = list(csv.DictReader(io.StringIO(export_archive('DE', severity='high').lstrip('\ufeff')), delimiter=';'))
    assert len(exported) == 1 and exported[0]['run_id']
    for kwargs in ({'limit': 0}, {'limit': 31}, {'date_from': 'bad'}, {'severity': 'invalid'}):
        with pytest.raises(ValueError):
            archived_alerts('DE', **kwargs)


def test_archive_endpoints_auth_and_validation(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            paths = ('/admin/live-check/archive', '/admin/live-check/archive/export.csv')
            for path in paths:
                assert (await client.get(path)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            assert (await client.get(paths[0])).status_code == 200
            csv_response = await client.get(paths[1])
            assert csv_response.status_code == 200
            assert csv_response.content.startswith(b'\xef\xbb\xbf')
            assert csv_response.headers['cache-control'] == 'no-store'
            for path in paths:
                assert (await client.get(path, params={'language': 'XX'})).status_code == 422
                assert (await client.get(path, params={'limit': 100})).status_code == 422
    asyncio.run(run())
