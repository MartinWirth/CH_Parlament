"""Read-only prioritized alerts from the latest persisted link checks."""
from .social_check_history import _connect, LANGUAGES, compare_reports
import json


def severity(item):
    status = item.get('status')
    if status == 'possibly_missing':
        return 'high'
    if status in ('request_failed', 'redirect', 'uncertain'):
        return 'medium'
    if status == 'restricted_or_rate_limited':
        return 'low'
    return None


def latest_alerts(language):
    if language not in LANGUAGES:
        raise ValueError('Invalid language')
    with _connect() as db:
        rows = db.execute('SELECT checked_at, report_json FROM checks WHERE language=? ORDER BY id DESC LIMIT 2', (language,)).fetchall()
    if not rows:
        return {'language': language, 'checked_at': None, 'comparison_available': False, 'alerts': [], 'counts': {'high': 0, 'medium': 0, 'low': 0, 'new': 0}}
    current = json.loads(rows[0][1])
    previous = json.loads(rows[1][1]) if len(rows) > 1 else None
    changes = compare_reports(previous, current) if previous else []
    changed = {(v['person_number'], v['platform'], v['url']): v for v in changes}
    alerts = []
    for item in current.get('results', []):
        level = severity(item)
        if not level:
            continue
        key = (item.get('person_number'), item.get('platform'), item.get('url'))
        change = changed.get(key)
        # A first observation is unconfirmed, not a newly broken profile.
        is_new = bool(change and change.get('previous_status') == 'reachable')
        alerts.append({'person_number': item.get('person_number'), 'platform': item.get('platform'),
                       'url': item.get('url'), 'status': item.get('status'),
                       'http_status': item.get('http_status'), 'severity': level,
                       'new_problem': is_new, 'previous_status': change.get('previous_status') if change else None})
    priority = {'high': 0, 'medium': 1, 'low': 2}
    alerts.sort(key=lambda item: (not item['new_problem'], priority[item['severity']], str(item['person_number'])))
    counts = {level: sum(v['severity'] == level for v in alerts) for level in priority}
    counts['new'] = sum(v['new_problem'] for v in alerts)
    return {'language': language, 'checked_at': rows[0][0], 'comparison_available': previous is not None,
            'alerts': alerts, 'counts': counts}
