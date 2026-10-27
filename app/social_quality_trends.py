"""Historical data quality indicators from persisted check runs.

Offline link quality snapshots are recorded only for new checks. Missing values
in older reports stay null rather than being reconstructed from today's data.
"""
import json
from .social_check_history import _connect, LANGUAGES, MAX_RUNS
from .social_alerts import severity


def quality_trends(language, *, limit=10):
    if language not in LANGUAGES or type(limit) is not int or not 1 <= limit <= MAX_RUNS:
        raise ValueError('Invalid quality trend query')
    with _connect() as db:
        rows = db.execute("""SELECT checked_at, report_json FROM checks
            WHERE language=? ORDER BY id DESC LIMIT ?""", (language, limit)).fetchall()
    points = []
    for checked_at, payload in reversed(rows):
        report = json.loads(payload)
        results = report.get('results', [])
        if not isinstance(results, list):
            results = []
        checked = len(results)
        reachable = sum(item.get('status') == 'reachable' for item in results if isinstance(item, dict))
        flagged = sum(severity(item) is not None for item in results if isinstance(item, dict))
        quality = report.get('quality_snapshot')
        quality = quality if isinstance(quality, dict) else {}
        points.append({
            'checked_at': checked_at, 'checked': checked, 'reachable': reachable,
            'reachable_percent': round(reachable / checked * 100, 1) if checked else None,
            'flagged': flagged,
            'flagged_percent': round(flagged / checked * 100, 1) if checked else None,
            'invalid': quality.get('invalid') if type(quality.get('invalid')) is int else None,
            'duplicates': quality.get('duplicates') if type(quality.get('duplicates')) is int else None,
            'snapshot_links': quality.get('checked') if type(quality.get('checked')) is int else None,
            'truncated': bool(report.get('truncated')),
        })
    return {
        'language': language, 'runs': len(points), 'trend': points,
        'note': 'Reachability and flagged values reflect links checked in each run, not all profiles. '
                'Historical manual review states are unavailable. Offline quality values are only '
                'available for runs recorded after quality snapshots were introduced.',
    }
