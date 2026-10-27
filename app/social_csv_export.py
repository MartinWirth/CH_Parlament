"""Authenticated admin CSV exports of alerts and persisted technical check metrics."""
import csv
import io
from .social_alerts import latest_alerts
from .social_alert_metrics import alert_metrics

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


def export_alerts(language):
    report = latest_alerts(language)
    rows = [dict(item, language=language, checked_at=report['checked_at']) for item in report['alerts']]
    return render_csv(ALERT_COLUMNS, rows)


def export_trends(language, limit=10):
    metrics = alert_metrics(language, limit)
    rows = [dict(item, language=language) for item in metrics['trend']]
    return render_csv(TREND_COLUMNS, rows)
