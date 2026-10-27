"""Historical technical alert observations from retained completed checks.

This is not a historical triage-state ledger; manual decisions live in audit events.
"""
import json
from .social_check_history import _connect, LANGUAGES, MAX_RUNS, _key
from .social_alerts import severity
from .social_alert_filters import filter_alerts


def archived_alerts(language, *, limit=30, search='', platform='', severity='all',
                    date_from='', date_to='', new_only=False):
    if language not in LANGUAGES or type(limit) is not int or not 1 <= limit <= MAX_RUNS:
        raise ValueError('Invalid archive query')
    with _connect() as db:
        rows = db.execute(
            'SELECT id, checked_at, report_json FROM checks WHERE language=? ORDER BY id DESC LIMIT ?',
            (language, limit + 1)).fetchall()
    observations = []
    for i, (run_id, checked_at, raw) in enumerate(rows[:limit]):
        report = json.loads(raw)
        previous = json.loads(rows[i + 1][2]) if i + 1 < len(rows) else None
        prior = {_key(item): item for item in previous.get('results', []) if _key(item)} if previous else {}
        run_alerts = []
        for item in report.get('results', []):
            level = severity(item)
            if not level:
                continue
            key = _key(item)
            earlier = prior.get(key)
            is_new = bool(earlier and earlier.get('status') == 'reachable')
            run_alerts.append({
                'run_id': run_id, 'checked_at': checked_at, 'person_number': item.get('person_number'),
                'platform': item.get('platform'), 'url': item.get('url'), 'status': item.get('status'),
                'http_status': item.get('http_status'), 'severity': level, 'new_problem': is_new,
                'previous_status': earlier.get('status') if earlier else None,
            })
        observations.extend(filter_alerts(run_alerts, search=search, platform=platform,
                                          severity=severity, date_from=date_from,
                                          date_to=date_to, new_only=new_only,
                                          checked_at=checked_at))
    return {'language': language, 'runs': min(limit, len(rows)), 'total': len(observations),
            'alerts': observations,
            'note': 'Technical observations only. Manual review states are not reconstructed historically.'}
