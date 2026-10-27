"""Consistent filtering for current alerts and their CSV export."""
from datetime import date, datetime

SEVERITIES = ('all', 'high', 'medium', 'low')
STATES = ('all', 'open', 'in_progress', 'resolved', 'ignored')


def filter_alerts(alerts, *, search='', platform='', severity='all', state='all',
                  date_from='', date_to='', new_only=False, checked_at=None):
    """Date range applies to the latest check timestamp, not a historical alert feed."""
    if severity not in SEVERITIES or state not in STATES or type(new_only) is not bool:
        raise ValueError('Invalid alert filters')
    if any(not isinstance(v, str) or len(v) > 200 for v in (search, platform, date_from, date_to)):
        raise ValueError('Invalid alert filters')
    try:
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
    except ValueError as exc:
        raise ValueError('Invalid date filter') from exc
    if start and end and start > end:
        raise ValueError('Invalid date range')
    if start or end:
        if not checked_at:
            return []
        try:
            checked_date = datetime.fromisoformat(checked_at.replace('Z', '+00:00')).date()
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid check timestamp') from exc
        if (start and checked_date < start) or (end and checked_date > end):
            return []
    needle = search.strip().casefold()
    platform_needle = platform.strip().casefold()
    return [item for item in alerts
            if (not needle or any(needle in str(item.get(key) or '').casefold()
                                  for key in ('person_number', 'platform', 'url', 'review_reason')))
            and (not platform_needle or str(item.get('platform') or '').casefold() == platform_needle)
            and (severity == 'all' or item.get('severity') == severity)
            and (state == 'all' or item.get('review_state') == state)
            and (not new_only or item.get('new_problem') is True)]
