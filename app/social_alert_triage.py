"""Persistent manual alert triage, separate from verified social profiles."""
from datetime import datetime, timezone
from .social_check_history import _connect, LANGUAGES
from .social_quality import canonical_url

STATES = ('open', 'in_progress', 'resolved', 'ignored')


def key_parts(language, person_number, platform, url):
    if language not in LANGUAGES or not isinstance(person_number, str) or not person_number.strip() or len(person_number) > 100:
        raise ValueError('Invalid language or member')
    if not isinstance(platform, str) or not platform.strip() or len(platform) > 100:
        raise ValueError('Invalid platform')
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Invalid URL')
    normalized = canonical_url(url)
    if not normalized or not normalized.startswith('https://'):
        raise ValueError('Invalid URL')
    return language, person_number, platform, normalized


def _table(db):
    db.execute('''CREATE TABLE IF NOT EXISTS alert_triage (
        language TEXT NOT NULL, person_number TEXT NOT NULL,
        platform TEXT NOT NULL, canonical_url TEXT NOT NULL,
        state TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY (language, person_number, platform, canonical_url))''')


def decisions(language):
    if language not in LANGUAGES:
        raise ValueError('Invalid language')
    with _connect() as db:
        _table(db)
        rows = db.execute('SELECT person_number, platform, canonical_url, state, reason, updated_at FROM alert_triage WHERE language=?', (language,)).fetchall()
    return {(member, platform, url): {'state': state, 'reason': reason, 'updated_at': stamp}
            for member, platform, url, state, reason, stamp in rows}


def save_decision(language, person_number, platform, url, state, reason):
    key = key_parts(language, person_number, platform, url)
    if state not in STATES or not isinstance(reason, str) or len(reason) > 1000:
        raise ValueError('Invalid state or reason')
    if state in ('resolved', 'ignored') and not reason.strip():
        raise ValueError('A reason is required for resolved or ignored alerts')
    stamp = datetime.now(timezone.utc).isoformat()
    with _connect() as db:
        _table(db)
        db.execute('''INSERT INTO alert_triage
            (language, person_number, platform, canonical_url, state, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(language, person_number, platform, canonical_url)
            DO UPDATE SET state=excluded.state, reason=excluded.reason, updated_at=excluded.updated_at''',
            (*key, state, reason.strip(), stamp))
    return {'state': state, 'reason': reason.strip(), 'updated_at': stamp}
