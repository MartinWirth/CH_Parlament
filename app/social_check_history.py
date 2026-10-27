"""Durable, bounded SQLite history of opt-in social link checks.

Only completed checks are saved. Comparisons are made by member, platform and
canonical URL, never by list position. This module does not modify profiles.
"""
import json
import os
from pathlib import Path
import sqlite3

from .social_quality import canonical_url

MAX_RUNS = 30
LANGUAGES = ("DE", "FR", "IT", "RM")


def history_path():
    return Path(os.getenv("SOCIAL_CHECK_HISTORY_PATH", ".social_check_history.sqlite3"))


def _connect():
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("""CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        language TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        report_json TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_checks_language ON checks(language, id)")
    return connection


def _key(item):
    if not isinstance(item, dict):
        return None
    number, platform, url = item.get("person_number"), item.get("platform"), item.get("url")
    canonical = canonical_url(url)
    if not isinstance(number, str) or not isinstance(platform, str) or not canonical:
        return None
    return (number, platform, canonical)


def compare_reports(previous, current):
    """Changes only for links checked in both runs; no missing-link inference."""
    old = {_key(item): item for item in previous.get("results", [])
           if _key(item) is not None}
    changes = []
    for item in current.get("results", []):
        key = _key(item)
        if key is None or key not in old:
            continue
        before = old[key]
        if before.get("status") != item.get("status") or before.get("http_status") != item.get("http_status"):
            changes.append({
                "person_number": key[0], "platform": key[1], "url": item["url"],
                "previous_status": before.get("status"), "status": item.get("status"),
                "previous_http_status": before.get("http_status"),
                "http_status": item.get("http_status"),
                "needs_review": item.get("status") in ("possibly_missing", "request_failed", "redirect", "uncertain"),
            })
    return changes


def save_report(language, report):
    if language not in LANGUAGES:
        raise ValueError("Invalid language")
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("Invalid report")
    payload = json.dumps(report, ensure_ascii=False)
    if len(payload) > 2_000_000:
        raise ValueError("Report too large")
    with _connect() as connection:
        connection.execute("INSERT INTO checks(language, checked_at, report_json) VALUES (?, ?, ?)",
                           (language, str(report.get("checked_at", "")), payload))
        connection.execute("""DELETE FROM checks WHERE language = ? AND id NOT IN
            (SELECT id FROM checks WHERE language = ? ORDER BY id DESC LIMIT ?)""",
                           (language, language, MAX_RUNS))


def get_history(language, limit=10):
    if language not in LANGUAGES or not isinstance(limit, int) or not 1 <= limit <= MAX_RUNS:
        raise ValueError("Invalid history query")
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, checked_at, report_json FROM checks WHERE language=? ORDER BY id DESC LIMIT ?",
            (language, limit)).fetchall()
    reports = [(id_, checked_at, json.loads(payload)) for id_, checked_at, payload in rows]
    summaries = []
    for index, (id_, checked_at, report) in enumerate(reports):
        previous = reports[index + 1][2] if index + 1 < len(reports) else None
        results = report.get("results", [])
        flagged = [item for item in results if item.get("status") in
                   ("possibly_missing", "request_failed", "redirect", "uncertain")]
        summaries.append({
            "id": id_, "checked_at": checked_at, "checked": report.get("checked", len(results)),
            "truncated": bool(report.get("truncated")),
            "flagged": len(flagged), "flagged_results": flagged,
            "changes": compare_reports(previous, report) if previous else [],
            "comparison_available": previous is not None,
        })
    return summaries
