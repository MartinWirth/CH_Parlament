"""Authenticated admin CSV exports of alerts and persisted technical check metrics."""
import csv
import io
from .social_alerts import latest_alerts
from .social_alert_metrics import alert_metrics
from .social_alert_filters import filter_alerts

ALERT_COLUMNS = ('language', 'checked_at', 'person_number', 'platform', 'url',
                 'status', 'http_status', 'severity', 'new_problem', 'previous_status',
                 'review_state', 'review_reason', 'review_updated_at')
TREND_COLUMNS = ('language', 'checked_at', 'checked', 'flagged', 'high', 'medium', 'low')


def safe_cell(value):
    """Prevent CSV spreadsheet formula evaluation, including whitespace prefixes."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    value = str(value)
    if value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def render_csv(columns, records):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
    writer.writerow(columns)
    for record in records:
        writer.writerow([safe_cell(record.get(column)) for column in columns])
    return '\ufeff' + buffer.getvalue()


def export_alerts(language, *, search="", platform="", severity="all", state="all",\n                  date_from="", date_to="", new_only=False):
    report = latest_alerts(language)
    filtered = filter_alerts(report['alerts'], search=search, platform=platform, severity=severity,\n                             state=state, date_from=date_from, date_to=date_to,\n                             new_only=new_only, checked_at=report['checked_at'])\n    rows = [dict(item, language=language, checked_at=report['checked_at']) for item in filtered]
    return render_csv(ALERT_COLUMNS, rows)


def export_trends(language, limit=10):
    metrics = alert_metrics(language, limit)
    rows = [dict(item, language=language) for item in metrics['trend']]
    return render_csv(TREND_COLUMNS, rows)
