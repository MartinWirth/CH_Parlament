"""Read-only data health summary for authenticated administrators."""
from datetime import datetime, timezone
from .social_check_history import LANGUAGES, latest_check_time
from .social_alert_metrics import alert_metrics
from .social_quality import audit_profiles
from .social_legacy_triage import legacy_candidates


def data_health(language, *, stale_hours=48, now=None):
    if language not in LANGUAGES or type(stale_hours) is not int or not 1 <= stale_hours <= 24 * 30:
        raise ValueError('Invalid health query')
    from .main import read_profile_snapshot, verified_social_url
    from .social_review import apply_decisions
    snapshot = read_profile_snapshot()
    apply_decisions(snapshot, language)
    data = snapshot.get(language, {})
    profiles = data.get('profiles', {})
    quality = audit_profiles(profiles, verified_social_url)
    metrics = alert_metrics(language, limit=1)['summary']
    legacy = legacy_candidates(language)
    checked_at = latest_check_time(language)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('Timezone-aware time required')
    age_hours = None
    if checked_at:
        try:
            timestamp = datetime.fromisoformat(checked_at.replace('Z', '+00:00'))
            if timestamp.tzinfo is not None:
                age_hours = max(0, (current - timestamp).total_seconds() / 3600)
        except (ValueError, OverflowError, TypeError):
            pass
    stale = age_hours is None or age_hours > stale_hours
    warnings = []
    if stale:
        warnings.append({'code': 'check_stale' if checked_at else 'check_missing',
                         'message': 'Letzte Online-Prüfung ist veraltet oder nicht vorhanden.'})
    if quality['invalid']:
        warnings.append({'code': 'invalid_links', 'message': 'Ungültige Profil-Links gefunden.'})
    if quality['duplicates']:
        warnings.append({'code': 'duplicate_links', 'message': 'Mehrfach zugeordnete Profil-Links gefunden.'})
    if metrics['active']:
        warnings.append({'code': 'active_alerts', 'message': 'Offene technische Warnmeldungen vorhanden.'})
    if legacy['count']:
        warnings.append({'code': 'legacy_facebook', 'message': 'Alte Facebook-Zuordnungen manuell prüfen.'})
    return {
        'language': language,
        'snapshot_updated_at': data.get('updated_at'),
        'members_with_profiles': sum(bool(links) for links in profiles.values()) if isinstance(profiles, dict) else 0,
        'verified_links': quality['checked'],
        'quality': {'invalid': quality['invalid'], 'duplicates': quality['duplicates'],
                    'issue_count': quality['issue_count']},
        'alerts': {'active': metrics['active'], 'high_active': metrics['high_active'],
                   'total': metrics['total']},
        'legacy_facebook': legacy['count'],
        'last_check_at': checked_at,
        'last_check_age_hours': round(age_hours, 1) if age_hours is not None else None,
        'stale_threshold_hours': stale_hours,
        'stale': stale,
        'warnings': warnings,
        'note': 'Counts describe the selected language and stored snapshot. A live check may cover at most 100 links; this is not a complete availability guarantee.',
    }
