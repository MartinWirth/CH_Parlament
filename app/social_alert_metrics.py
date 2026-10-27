"""Admin alert metrics from persisted checks and current manual triage."""
import json
from .social_check_history import _connect, LANGUAGES, MAX_RUNS
from .social_alerts import latest_alerts, severity


def alert_metrics(language, limit=10):
    if language not in LANGUAGES or type(limit) is not int or not 1 <= limit <= MAX_RUNS:
        raise ValueError('Invalid metrics query')
    current = latest_alerts(language)
    alerts = current['alerts']
    states = ('open', 'in_progress', 'resolved', 'ignored')
    counts = {state: sum(item['review_state'] == state for item in alerts) for state in states}
    active = [item for item in alerts if item['review_state'] in ('open', 'in_progress')]
    summary = {'checked_at': current['checked_at'], 'total': len(alerts),
               'active': len(active), 'high_active': sum(item['severity'] == 'high' for item in active),
               'new': current['counts']['new'], 'states': counts,
               'severity': current['counts']}
    with _connect() as db:
        rows = db.execute('''SELECT checked_at, report_json FROM checks
            WHERE language=? ORDER BY id DESC LIMIT ?''', (language, limit)).fetchall()
    trend = []
    for checked_at, raw in reversed(rows):
        report = json.loads(raw)
        results = report.get('results', [])
        by_severity = {'high': 0, 'medium': 0, 'low': 0}
        for item in results:
            level = severity(item)
            if level:
                by_severity[level] += 1
        trend.append({'checked_at': checked_at, 'checked': len(results),
                      'flagged': sum(by_severity.values()), **by_severity})
    return {'language': language, 'summary': summary, 'trend': trend,
            'trend_note': 'Historical values reflect check results at that time, not historical manual triage states.'}
