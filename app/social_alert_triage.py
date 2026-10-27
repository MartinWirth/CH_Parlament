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


def _events_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS alert_triage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        language TEXT NOT NULL, person_number TEXT NOT NULL,
        platform TEXT NOT NULL, canonical_url TEXT NOT NULL,
        old_state TEXT, new_state TEXT NOT NULL,
        reason TEXT NOT NULL, updated_at TEXT NOT NULL,
        source TEXT NOT NULL
    )""")


def events(language, person_number, platform, url):
    key = key_parts(language, person_number, platform, url)
    with _connect() as db:
        _events_table(db)
        rows = db.execute("""SELECT old_state, new_state, reason, updated_at, source
            FROM alert_triage_events
            WHERE language=? AND person_number=? AND platform=? AND canonical_url=?
            ORDER BY id ASC""", key).fetchall()
    return [{'old_state': old, 'new_state': new, 'reason': reason,
             'updated_at': stamp, 'source': source}
            for old, new, reason, stamp, source in rows]


def reopen_on_recurrence(db, language, previous, current):
    """Reopen closed alerts only after an observed reachable -> problematic transition.

    Called inside the same transaction that saves the completed check.
    """
    from .social_check_history import _key
    from .social_alerts import severity
    if previous is None:
        return 0
    _table(db)
    _events_table(db)
    old = {_key(item): item for item in previous.get('results', []) if _key(item)}
    stamp = datetime.now(timezone.utc).isoformat()
    reopened = 0
    for item in current.get('results', []):
        key = _key(item)
        if key is None or key not in old:
            continue
        if old[key].get('status') != 'reachable' or severity(item) is None:
            continue
        identity = (language, *key)
        row = db.execute("""SELECT state FROM alert_triage
            WHERE language=? AND person_number=? AND platform=? AND canonical_url=?""", identity).fetchone()
        if not row or row[0] not in ('resolved', 'ignored'):
            continue
        reason = 'Automatisch wiedereröffnet: Link nach erreichbarer Prüfung erneut auffällig'
        db.execute("""UPDATE alert_triage SET state='open', reason=?, updated_at=?
            WHERE language=? AND person_number=? AND platform=? AND canonical_url=?""",
            (reason, stamp, *identity))
        db.execute("""INSERT INTO alert_triage_events
            (language, person_number, platform, canonical_url, old_state, new_state, reason, updated_at, source)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?, 'automatic')""",
            (*identity, row[0], reason, stamp))
        reopened += 1
    return reopened


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
        existing = db.execute('''SELECT state FROM alert_triage WHERE language=? AND person_number=? AND platform=? AND canonical_url=?''', key).fetchone()
        _events_table(db)
        db.execute('''INSERT INTO alert_triage
            (language, person_number, platform, canonical_url, state, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(language, person_number, platform, canonical_url)
            DO UPDATE SET state=excluded.state, reason=excluded.reason, updated_at=excluded.updated_at''',
            (*key, state, reason.strip(), stamp))
        db.execute("""INSERT INTO alert_triage_events
            (language, person_number, platform, canonical_url, old_state, new_state, reason, updated_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual')""",
            (*key, existing[0] if existing else None, state, reason.strip(), stamp))
    return {'state': state, 'reason': reason.strip(), 'updated_at': stamp}
