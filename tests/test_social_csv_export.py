"""CSV export correctness, Excel compatibility, injection protection and auth."""
import asyncio
import csv
import io
import httpx
from app import admin
from app.main import app
from app.social_check_history import save_report
from app.social_alert_triage import save_decision
from app.social_csv_export import export_alerts, export_trends, safe_cell

URL = 'https://instagram.com/member'


def report(status, code, stamp):
    return {'checked_at': stamp, 'results': [
        {'person_number': '42', 'platform': 'Instagram', 'url': URL,
         'status': status, 'http_status': code}]}


def rows(data):
    return list(csv.DictReader(io.StringIO(data.lstrip('\ufeff')), delimiter=';'))


def test_export_alerts_and_trends(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    assert rows(export_alerts('DE')) == []
    save_report('DE', report('reachable', 200, '2026-10-26'))
    save_report('DE', report('possibly_missing', 404, '2026-10-27'))
    save_decision('DE', '42', 'Instagram', URL, 'in_progress', 'Überprüfung läuft')
    output = export_alerts('DE')
    assert output.startswith('\ufeff')
    alert = rows(output)[0]
    assert alert['review_reason'] == 'Überprüfung läuft'
    assert alert['severity'] == 'high'
    assert alert['new_problem'] == 'true'
    assert alert['language'] == 'DE'
    assert [int(row['flagged']) for row in rows(export_trends('DE'))] == [0, 1]
    assert [int(row['flagged']) for row in rows(export_trends('DE', 1))] == [1]
    assert rows(export_alerts('FR')) == []


def test_spreadsheet_formula_protection():
    for value in ('=1+1', '+SUM(A1)', '-1+2', '@SUM(A1)', '  =1+1', '\t=1+1', '\n=1+1'):
        assert safe_cell(value).startswith("'")
    assert safe_cell('Normal text') == 'Normal text'
    assert safe_cell(None) == ''


def test_csv_endpoints_require_login(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            paths = ('/admin/live-check/alerts/export.csv', '/admin/live-check/metrics/export.csv')
            for path in paths:
                assert (await client.get(path)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            for path in paths:
                response = await client.get(path)
                assert response.status_code == 200
                assert response.content.startswith(b'\xef\xbb\xbf')
                assert 'attachment;' in response.headers['content-disposition']
                assert response.headers['cache-control'] == 'no-store'
                assert (await client.get(path, params={'language': 'XX'})).status_code == 422
            assert (await client.get(paths[1], params={'limit': 100})).status_code == 422
    asyncio.run(run())
