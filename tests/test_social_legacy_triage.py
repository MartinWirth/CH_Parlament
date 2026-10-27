"""Legacy Facebook triage review must never silently assign a shared old key."""
import asyncio
import httpx
import pytest
from app import admin
from app.main import app
from app.social_check_history import save_report, _connect
from app.social_alert_triage import _table, _events_table, decisions, events
from app.social_legacy_triage import legacy_candidates, resolve_legacy

OLD = 'https://www.facebook.com/profile.php'
A = OLD + '?id=123'
B = OLD + '?id=456'


def seed(monkeypatch, tmp_path):
    monkeypatch.setenv('SOCIAL_CHECK_HISTORY_PATH', str(tmp_path / 'history.sqlite3'))
    with _connect() as db:
        _table(db)
        _events_table(db)
        db.execute("""INSERT INTO alert_triage VALUES
            ('DE', '42', 'Facebook', ?, 'resolved', 'Old shared key', '2026-10-01')""", (OLD,))
        db.execute("""INSERT INTO alert_triage_events
            (language, person_number, platform, canonical_url, old_state, new_state, reason, updated_at, source)
            VALUES ('DE', '42', 'Facebook', ?, NULL, 'resolved', 'Old shared key', '2026-10-01', 'manual')""", (OLD,))
    save_report('DE', {'checked_at': '2026-10-27T10:00:00+00:00',
        'results': [{'person_number': '42', 'platform': 'Facebook', 'url': url,
                     'status': 'possibly_missing', 'http_status': 404} for url in (A, B)]})


def test_legacy_ambiguous_requires_explicit_identity(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path)
    finding = legacy_candidates('DE')
    assert finding['count'] == 1
    assert finding['items'][0]['ambiguous'] is True
    assert set(finding['items'][0]['candidate_urls']) == {A, B}
    assert ('42', 'Facebook', OLD) in decisions('DE')
    with pytest.raises(ValueError):
        resolve_legacy('DE', '42', 'Facebook', OLD, B, 'resolved', '')
    with pytest.raises(ValueError):
        resolve_legacy('DE', '42', 'Facebook', OLD, OLD, 'resolved', 'bad')
    result = resolve_legacy('DE', '42', 'Facebook', OLD, A, 'in_progress', 'Reviewed account ID 123')
    assert result['legacy_preserved']
    rows = decisions('DE')
    assert rows[('42', 'Facebook', OLD)]['state'] == 'resolved'
    assert rows[('42', 'Facebook', A)]['state'] == 'in_progress'
    assert ('42', 'Facebook', B) not in rows
    assert len(events('DE', '42', 'Facebook', A)) == 1
    assert events('DE', '42', 'Facebook', OLD)[0]['source'] == 'manual'
    with pytest.raises(ValueError):
        resolve_legacy('DE', '42', 'Facebook', OLD, A, 'resolved', 'Again')


def test_legacy_review_endpoints_require_auth_and_origin(monkeypatch, tmp_path):
    seed(monkeypatch, tmp_path)
    monkeypatch.setenv('ADMIN_REFRESH_PASSWORD', 'secret')
    monkeypatch.setenv('SOCIAL_REFRESH_TOKEN', 'token')
    admin._sessions.clear()
    payload = {'language': 'DE', 'person_number': '42', 'platform': 'Facebook',
               'legacy_url': OLD, 'target_url': A, 'state': 'resolved', 'reason': 'Verified account'}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            path = '/admin/live-check/facebook-legacy'
            review = path + '/review'
            assert (await client.get(path)).status_code == 401
            assert (await client.post(review, json=payload)).status_code == 401
            headers = {'Origin': 'https://example.test', 'X-Admin-Action': '1'}
            assert (await client.post('/admin/login', json={'password': 'secret'}, headers=headers)).status_code == 200
            assert (await client.get(path)).json()['count'] == 1
            assert (await client.get(path, params={'language': 'XX'})).status_code == 422
            assert (await client.post(review, json=payload)).status_code == 403
            assert (await client.post(review, json=payload, headers=headers)).status_code == 200
            assert (await client.post(review, json=payload, headers=headers)).status_code == 422
    asyncio.run(run())
