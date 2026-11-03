"""Transactional SQLite persistence for directory reviews and audit history."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MAX_AUDIT_EVENTS = 1000


def database_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_DB_PATH", ".social_directory_review.sqlite3"))


@contextmanager
def connection():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    try:
        db.execute("PRAGMA busy_timeout=10000")
        # Concurrent first-time connections can race while switching journal mode.
        # Retry only transient SQLite lock contention, keeping WAL durability.
        for attempt in range(5):
            try:
                db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
        db.execute("PRAGMA synchronous=FULL")
        db.execute("""CREATE TABLE IF NOT EXISTS reviews (
            language TEXT NOT NULL, url TEXT NOT NULL, status TEXT NOT NULL,
            comment TEXT NOT NULL, reviewed_at TEXT NOT NULL,
            PRIMARY KEY(language, url))""")
        db.execute("""CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language TEXT NOT NULL, url TEXT NOT NULL,
            changed_at TEXT NOT NULL, previous_status TEXT,
            new_status TEXT NOT NULL, previous_comment TEXT, comment TEXT NOT NULL)""")
        db.commit()
        yield db
    finally:
        db.close()


def _all(db):
    reviews = {}
    for language, url, status, comment, reviewed_at in db.execute(
            "SELECT language,url,status,comment,reviewed_at FROM reviews"):
        reviews.setdefault(language, {})[url] = {
            "status": status, "comment": comment, "reviewed_at": reviewed_at}
    fields = ("language", "url", "changed_at", "previous_status",
              "new_status", "previous_comment", "comment")
    audit = [dict(zip(fields, row)) for row in db.execute(
        "SELECT language,url,changed_at,previous_status,new_status,previous_comment,comment "
        "FROM audit ORDER BY id")]
    return reviews, audit


def read_state():
    with connection() as db:
        return _all(db)


def update(language, url, status, comment):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,comment,reviewed_at FROM reviews WHERE language=? AND url=?",
                         (language, url)).fetchone()
        previous = dict(zip(("status", "comment", "reviewed_at"), row)) if row else {}
        comment = comment.strip()
        if previous.get("status") == status and previous.get("comment") == comment:
            db.commit()
            return previous
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO audit(language,url,changed_at,previous_status,new_status,previous_comment,comment) "
                   "VALUES(?,?,?,?,?,?,?)",
                   (language, url, now, previous.get("status"), status, previous.get("comment"), comment))
        db.execute("INSERT INTO reviews(language,url,status,comment,reviewed_at) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(language,url) DO UPDATE SET status=excluded.status,"
                   "comment=excluded.comment,reviewed_at=excluded.reviewed_at",
                   (language, url, status, comment, now))
        db.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT ?)",
                   (MAX_AUDIT_EVENTS,))
        db.commit()
        return {"status": status, "comment": comment, "reviewed_at": now}


def backup_to(path):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        with sqlite3.connect(str(destination)) as target:
            db.backup(target)
    return read_state()


def restore_from(path):
    source = Path(path)
    if not source.exists():
        raise ValueError("No valid directory review backup")
    with sqlite3.connect(str(source)) as original:
        if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Invalid SQLite backup")
        with connection() as db:
            original.backup(db)
    return read_state()


def health():
    if not database_path().exists():
        return {"status": "empty", "backup_valid": False,
                "reviews_valid": True, "audit_valid": True, "last_recovery": None}
    with connection() as db:
        good = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return {"status": "healthy" if good else "degraded", "backup_valid": False,
            "reviews_valid": good, "audit_valid": good, "last_recovery": None}


def migration_status():
    """Persisted migration state; never claim success before commit."""
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS migration (name TEXT PRIMARY KEY, completed_at TEXT NOT NULL, review_count INTEGER NOT NULL, audit_count INTEGER NOT NULL)")
        db.commit()
        row = db.execute("SELECT completed_at,review_count,audit_count FROM migration WHERE name='legacy_json'").fetchone()
        return {"state": "completed", "completed_at": row[0],
                "reviews": row[1], "events": row[2]} if row else {"state": "not_migrated"}


def validate_legacy(reviews, audit):
    """Fail closed on malformed legacy input rather than silently dropping records."""
    from urllib.parse import urlparse
    from datetime import datetime
    if not isinstance(reviews, dict) or not isinstance(audit, list):
        raise ValueError("Invalid legacy review or audit structure")
    count = 0
    for language, records in reviews.items():
        if language not in ("DE", "FR", "IT", "RM") or not isinstance(records, dict):
            raise ValueError("Invalid legacy language or reviews")
        for url, record in records.items():
            if not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).hostname:
                raise ValueError("Invalid legacy URL")
            if not isinstance(record, dict) or record.get("status") not in ("in_review", "resolved", "deferred"):
                raise ValueError("Invalid legacy status")
            if not isinstance(record.get("comment"), str) or len(record["comment"]) > 1000:
                raise ValueError("Invalid legacy comment")
            if not isinstance(record.get("reviewed_at"), str):
                raise ValueError("Invalid legacy review timestamp")
            count += 1
    for event in audit:
        if not isinstance(event, dict) or event.get("language") not in ("DE", "FR", "IT", "RM"):
            raise ValueError("Invalid legacy audit language")
        if not isinstance(event.get("url"), str) or urlparse(event["url"]).scheme != "https" or not urlparse(event["url"]).hostname:
            raise ValueError("Invalid legacy audit URL")
        if event.get("new_status") not in ("in_review", "resolved", "deferred"):
            raise ValueError("Invalid legacy audit status")
        if event.get("previous_status") not in (None, "in_review", "resolved", "deferred"):
            raise ValueError("Invalid legacy previous status")
        if any(not isinstance(event.get(k), str) for k in ("changed_at", "comment")):
            raise ValueError("Invalid legacy audit timestamp or comment")
        if any(event.get(k) is not None and not isinstance(event[k], str) for k in ("previous_comment",)):
            raise ValueError("Invalid legacy previous comment")
    return count


def import_legacy(reviews, audit):
    """One atomic import, including a durable completion marker."""
    count = validate_legacy(reviews, audit)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS migration (name TEXT PRIMARY KEY, completed_at TEXT NOT NULL, review_count INTEGER NOT NULL, audit_count INTEGER NOT NULL)")
        if db.execute("SELECT 1 FROM migration WHERE name='legacy_json'").fetchone():
            db.rollback()
            return False
        if db.execute("SELECT 1 FROM reviews LIMIT 1").fetchone() or db.execute(
                "SELECT 1 FROM audit LIMIT 1").fetchone():
            db.rollback()
            return False
        try:
            for language, records in reviews.items():
                for url, record in records.items():
                    db.execute("INSERT INTO reviews VALUES(?,?,?,?,?)",
                               (language, url, record["status"], record["comment"], record["reviewed_at"]))
            for event in audit[-MAX_AUDIT_EVENTS:]:
                db.execute("INSERT INTO audit(language,url,changed_at,previous_status,new_status,previous_comment,comment) VALUES(?,?,?,?,?,?,?)",
                           tuple(event.get(k) for k in ("language", "url", "changed_at", "previous_status",
                                                        "new_status", "previous_comment", "comment")))
            db.execute("INSERT INTO migration VALUES('legacy_json',?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), count, len(audit[-MAX_AUDIT_EVENTS:])))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return True
