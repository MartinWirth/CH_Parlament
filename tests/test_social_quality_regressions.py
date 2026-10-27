"""Regression detection on consecutive persisted runs."""
import asyncio
import httpx
import pytest
from app import admin
from app.main import app
from app.social_check_history import save_report
from app.social_quality_regressions import quality_regressions


def report(at, statuses, invalid=None, duplicates=None, truncated=False):
    rows = [{'person_number': str(i), 'platform': 'Instagram',
             'url': 'https://instagram.com/user' + str(i),
             'status': status, 'http_status': 200 if status == 'reachable' else 404}
            for i, status in enumerate(statuses)]
    result = {'checked_at': at, 'results': rows, 'truncated': truncated}
    if invalid is not None:
        result['quality_snapshot'] = {'checked': len(rows), 'invalid': invalid,
                                      'duplicates': duplicates}
    return result


def test_deterioration_and_missing_snapshots(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    save_report('DE', report('2026-10-25', ['reachable'] * 10))
    save_report('DE', report('2026-10-26', ['reachable'] * 8 + ['possibly_missing'] * 2,
                             invalid=1, duplicates=2))
    save_report('DE', report('2026-10-27', ['reachable'] * 5 + ['possibly_missing'] * 5,
                             invalid=3, duplicates=4))
    result = quality_regressions('DE')
    assert result['runs'] == 3
    assert sum(item['code'] == 'reachability_drop' for item in result['warnings']) == 2
    assert sum(item['code'] == 'flagged_rise' for item in result['warnings']) == 2
    assert sum(item['code'] == 'invalid_increase' for item in result['warnings']) == 1
    assert sum(item['code'] == 'duplicate_increase' for item in result['warnings']) == 1
    assert len(result['latest_warnings']) == 4
    assert quality_regressions('DE', reachability_drop=40)['latest_warnings'][0]['code'] != 'reachability_drop'


def test_validation_and_empty(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    assert quality_regressions('FR')['warnings'] == []
    for kwargs in ({'language': 'XX'}, {'language': 'DE', 'limit': 1},
                   {'language': 'DE', 'reachability_drop': -1},
                   {'language': 'DE', 'duplicate_increase': True}):
        with pytest.raises(ValueError):
            quality_regressions(**kwargs)


def test_authenticated_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url='https://example.test') as client:
            path = '/admin/data-health/regressions'
            assert (await client.get(path)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'},
                                      headers=headers)).status_code == 200
            assert (await client.get(path)).status_code == 200
            assert (await client.get(path, params={'reachability_drop': -1})).status_code == 422
    asyncio.run(run())
