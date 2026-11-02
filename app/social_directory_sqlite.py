"""Transactional SQLite persistence for directory reviews and audit history."""
import json
import os
import sqlite3
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
        db.execute("PRAGMA journal_mode=WAL")
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


def import_legacy(reviews, audit):
    """Import legacy data only into a fresh, empty database."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] or db.execute(
                "SELECT COUNT(*) FROM audit").fetchone()[0]:
            db.rollback()
            return False
        for language, records in reviews.items():
            if not isinstance(records, dict):
                continue
            for url, record in records.items():
                if not isinstance(record, dict):
                    continue
                db.execute("INSERT INTO reviews VALUES(?,?,?,?,?)",
                           (language, url, record.get("status", ""),
                            record.get("comment", ""), record.get("reviewed_at", "")))
        for event in audit[-MAX_AUDIT_EVENTS:]:
            if not isinstance(event, dict):
                continue
            db.execute("INSERT INTO audit(language,url,changed_at,previous_status,new_status,previous_comment,comment) "
                       "VALUES(?,?,?,?,?,?,?)",
                       tuple(event.get(k) for k in ("language", "url", "changed_at",
                                                    "previous_status", "new_status",
                                                    "previous_comment", "comment")))
        db.commit()
        return True
