"""Conservative review of legacy Facebook profile.php triage keys.

Legacy keys dropped the identity-bearing id parameter. Never infer an owner.
All historical events remain untouched; reviewed decisions are copied only
after explicit administrator confirmation to an exact current alert URL.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .social_check_history import _connect, LANGUAGES
from .social_quality import canonical_url
from .social_alert_triage import _table, _events_table, key_parts, STATES

LEGACY_HOSTS = ('facebook.com', 'www.facebook.com', 'm.facebook.com')


def _legacy_key(url):
    normalized = canonical_url(url)
    if not normalized:
        return None
    parts = urlsplit(normalized)
    if parts.hostname not in LEGACY_HOSTS or parts.path.lower() != '/profile.php':
        return None
    return parts._replace(query='', fragment='').geturl()


def legacy_candidates(language):
    if language not in LANGUAGES:
        raise ValueError('Invalid language')
    from .social_alerts import latest_alerts
    current = latest_alerts(language)['alerts']
    with _connect() as db:
        _table(db)
        rows = db.execute("""SELECT person_number, platform, canonical_url, state, reason, updated_at
            FROM alert_triage WHERE language=? AND lower(platform)='facebook'""", (language,)).fetchall()
    findings = []
    for member, platform, legacy_url, state, reason, updated in rows:
        if legacy_url not in ('https://' + host + '/profile.php' for host in LEGACY_HOSTS):
            continue
        matches = sorted({item['url'] for item in current
                          if item.get('person_number') == member
                          and str(item.get('platform', '')).casefold() == platform.casefold()
                          and _legacy_key(item.get('url')) == legacy_url
                          and canonical_url(item.get('url')) != legacy_url})
        findings.append({'person_number': member, 'platform': platform,
                         'legacy_url': legacy_url, 'state': state, 'reason': reason,
                         'updated_at': updated, 'candidate_urls': matches,
                         'ambiguous': len(matches) != 1,
                         'action': 'manual_review_required'})
    return {'language': language, 'count': len(findings), 'items': findings}


def resolve_legacy(language, person_number, platform, legacy_url, target_url, state, reason):
    """Explicitly copy a legacy decision; preserve legacy row and audit history.

    Revalidate current alert and legacy row under a write transaction.
    No automatic reassignment, even for a single candidate.
    """
    if not all(isinstance(v, str) for v in
               (language, person_number, platform, legacy_url, target_url, state, reason)):
        raise ValueError('Invalid resolution')
    if state not in STATES or len(reason) > 1000 or not reason.strip():
        raise ValueError('Explicit review reason required')
    if platform.casefold() != 'facebook' or _legacy_key(target_url) != legacy_url:
        raise ValueError('Mismatched Facebook identity')
    key = key_parts(language, person_number, platform, target_url)
    if key[3] == legacy_url:
        raise ValueError('Missing Facebook account ID')
    from .social_alerts import latest_alerts
    if not any(item.get('person_number') == person_number and
               item.get('platform') == platform and item.get('url') == target_url
               for item in latest_alerts(language)['alerts']):
        raise ValueError('Target is not a current alert')
    stamp = datetime.now(timezone.utc).isoformat()
    with _connect() as db:
        _table(db)
        _events_table(db)
        old = db.execute("""SELECT state FROM alert_triage
            WHERE language=? AND person_number=? AND platform=? AND canonical_url=?""",
            (language, person_number, platform, legacy_url)).fetchone()
        if not old:
            raise ValueError('Legacy decision no longer exists')
        existing = db.execute("""SELECT state FROM alert_triage
            WHERE language=? AND person_number=? AND platform=? AND canonical_url=?""", key).fetchone()
        if existing:
            raise ValueError('Target already has a decision; resolve manually in alert triage')
        db.execute("""INSERT INTO alert_triage
            (language, person_number, platform, canonical_url, state, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (*key, state, reason.strip(), stamp))
        db.execute("""INSERT INTO alert_triage_events
            (language, person_number, platform, canonical_url, old_state, new_state, reason, updated_at, source)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'legacy_manual_review')""",
            (*key, state, 'Legacy Facebook review (' + legacy_url + '): ' + reason.strip(), stamp))
    return {'ok': True, 'target_url': key[3], 'state': state,
            'legacy_preserved': True}
