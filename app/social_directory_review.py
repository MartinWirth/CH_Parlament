"""Persistent manual status for configured directory URLs; separate from scan results."""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

STATUSES = {"in_review", "resolved", "deferred"}
MAX_COMMENT = 1000
MAX_AUDIT_EVENTS = 1000


def _sqlite_enabled():
    """Use SQLite by default; custom legacy paths keep existing installations compatible."""
    backend = os.getenv("SOCIAL_DIRECTORY_STORAGE_BACKEND", "").lower()
    if backend:
        return backend == "sqlite"
    return not os.getenv("SOCIAL_DIRECTORY_REVIEW_PATH")


def _sqlite():
    from . import social_directory_sqlite
    return social_directory_sqlite


def _sqlite_backup_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH",
                           str(_sqlite().database_path()) + ".backup"))


def migration_status():
    if not _sqlite_enabled():
        return {"state": "legacy_json"}
    from .social_directory_sqlite import migration_status as sqlite_status
    return sqlite_status()


def migrate_legacy_to_sqlite():
    """Validate and preserve JSON inputs before an atomic one-time import."""
    if not _sqlite_enabled():
        return False
    from .social_directory_sqlite import import_legacy, validate_legacy
    if migration_status()["state"] == "completed":
        return False
    # A populated SQLite store must not be overwritten by old JSON.
    existing_reviews, existing_audit = _sqlite().read_state()
    if existing_reviews or existing_audit:
        return False
    paths = (review_path(), audit_path(), backup_path())
    if not any(path.exists() for path in paths):
        return False
    # A valid checkpoint is authoritative; never import partially corrupt files.
    checkpoint = _load_json(backup_path(), dict)
    if backup_path().exists() and (not isinstance(checkpoint, dict)
            or not isinstance(checkpoint.get("reviews"), dict)
            or not isinstance(checkpoint.get("audit"), list)):
        raise ValueError("Invalid legacy checkpoint; migration blocked")
    if checkpoint is None and (not review_path().exists() or not audit_path().exists()):
        raise ValueError("Incomplete legacy files; migration blocked")
    reviews, audit = _load_state()
    validate_legacy(reviews, audit)
    # Preserve every existing source file byte-for-byte before touching SQLite.
    import shutil
    import tempfile
    directory = Path(os.getenv("SOCIAL_DIRECTORY_MIGRATION_BACKUP_DIR",
                               str(_sqlite().database_path()) + ".legacy-backup"))
    directory.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(paths):
        if not source.exists():
            continue
        destination = directory / ("reviews.json", "audit.json", "checkpoint.json")[index]
        if destination.exists():
            if destination.read_bytes() != source.read_bytes():
                raise ValueError("Legacy migration backup differs; manual review required")
            continue
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
                temp = Path(handle.name)
                with source.open("rb") as origin:
                    shutil.copyfileobj(origin, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, destination)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
    return import_legacy(reviews, audit)


def review_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_REVIEW_PATH", ".social_directory_review.json"))


def read_reviews():
    return _sqlite().read_state()[0] if _sqlite_enabled() else _load_state()[0]


def audit_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(review_path()) + ".audit.json"))


def read_audit():
    return _sqlite().read_state()[1] if _sqlite_enabled() else _load_state()[1]


def audit_for_sources(language, sources):
    urls = {item.get("url") for item in sources if isinstance(item, dict)}
    return [event for event in read_audit()
            if isinstance(event, dict) and event.get("language") == language
            and event.get("url") in urls]


def backup_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_BACKUP_PATH", str(review_path()) + ".backup.json"))


def _atomic_json(path, data):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as handle:
            name = handle.name
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _load_json(path, expected):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, expected) else None
    except (OSError, ValueError):
        return None


def _load_state():
    reviews = _load_json(review_path(), dict)
    audit = _load_json(audit_path(), list)
    backup = _load_json(backup_path(), dict)
    # The backup is the commit marker for the pair of primary files.
    if backup and isinstance(backup.get("reviews"), dict) and isinstance(backup.get("audit"), list):
        return backup["reviews"], backup["audit"]
    if reviews is not None and audit is not None:
        return reviews, audit
    return reviews if reviews is not None else {}, audit if audit is not None else []


@contextmanager
def _storage_lock():
    """Exclusive cross-process lock; fail closed rather than overwrite another writer."""
    import time
    lock = Path(str(review_path()) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 10
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Directory review storage is locked")
            time.sleep(0.05)
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def create_backup():
    if _sqlite_enabled():
        reviews, audit = _sqlite().backup_to(_sqlite_backup_path())
        return {"reviews": len(reviews), "events": len(audit)}
    with _storage_lock():
        reviews, audit = _load_state()
        _atomic_json(backup_path(), {"reviews": reviews, "audit": audit})
        return {"reviews": len(reviews), "events": len(audit)}


def restore_backup():
    if _sqlite_enabled():
        reviews, audit = _sqlite().restore_from(_sqlite_backup_path())
        return {"reviews": len(reviews), "events": len(audit)}
    with _storage_lock():
        saved = _load_json(backup_path(), dict)
        if not saved or not isinstance(saved.get("reviews"), dict) or not isinstance(saved.get("audit"), list):
            raise ValueError("No valid directory review backup")
        _atomic_json(review_path(), saved["reviews"])
        _atomic_json(audit_path(), saved["audit"])
        return {"reviews": len(saved["reviews"]), "events": len(saved["audit"])}



def recovery_log_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_RECOVERY_LOG_PATH",
                           str(review_path()) + ".recovery.json"))


def storage_health():
    """Inspect files without silently masking corruption."""
    if _sqlite_enabled():
        result = _sqlite().health()
        result["migration"] = migration_status()
        from .social_directory_maintenance import status as maintenance_status
        result["maintenance"] = maintenance_status()
        result["backup_valid"] = result["maintenance"]["backup_valid"]
        return result
    reviews = _load_json(review_path(), dict)
    audit = _load_json(audit_path(), list)
    backup = _load_json(backup_path(), dict)
    valid_backup = (isinstance(backup, dict)
                    and isinstance(backup.get("reviews"), dict)
                    and isinstance(backup.get("audit"), list))
    missing = all(not path.exists() for path in (review_path(), audit_path(), backup_path()))
    if missing:
        state = "empty"
    elif not valid_backup:
        state = "degraded"
    elif reviews != backup["reviews"] or audit != backup["audit"]:
        state = "recovery_needed"
    else:
        state = "healthy"
    history = _load_json(recovery_log_path(), list)
    return {"status": state, "backup_valid": valid_backup,
            "reviews_valid": reviews is not None,
            "audit_valid": audit is not None,
            "last_recovery": history[-1] if history else None}


def verify_and_recover():
    """Restore mismatched primary files from the committed checkpoint."""
    if _sqlite_enabled():
        migrate_legacy_to_sqlite()
        return storage_health()
    with _storage_lock():
        before = storage_health()
        if before["status"] != "recovery_needed":
            return before
        saved = _load_json(backup_path(), dict)
        _atomic_json(review_path(), saved["reviews"])
        _atomic_json(audit_path(), saved["audit"])
        history = _load_json(recovery_log_path(), list) or []
        history.append({"at": datetime.now(timezone.utc).isoformat(),
                        "reason": "primary_files_mismatched_backup",
                        "restored_events": len(saved["audit"])})
        _atomic_json(recovery_log_path(), history[-100:])
        return storage_health()

def update_review(language, url, status, comment, configured_urls):
    if language not in ("DE", "FR", "IT", "RM") or status not in STATUSES:
        raise ValueError("Invalid language or status")
    if not isinstance(url, str) or url not in configured_urls:
        raise ValueError("Unknown configured source")
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT:
        raise ValueError("Invalid comment")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Invalid source URL")
    if _sqlite_enabled():
        return _sqlite().update(language, url, status, comment)
    with _storage_lock():
        data, audit = _load_state()
        previous = data.get(language, {}).get(url, {})
        if not isinstance(previous, dict):
            previous = {}
        record = {"status": status, "comment": comment.strip(),
                  "reviewed_at": datetime.now(timezone.utc).isoformat()}
        if previous.get("status") == status and previous.get("comment") == record["comment"]:
            return previous
        audit.append({"language": language, "url": url,
                      "changed_at": record["reviewed_at"],
                      "previous_status": previous.get("status"),
                      "new_status": status,
                      "previous_comment": previous.get("comment"),
                      "comment": record["comment"]})
        audit = audit[-MAX_AUDIT_EVENTS:]
        data.setdefault(language, {})[url] = record
        _atomic_json(backup_path(), {"reviews": data, "audit": audit})
        _atomic_json(review_path(), data)
        _atomic_json(audit_path(), audit)
        if storage_health()["status"] != "healthy":
            raise OSError("Directory review storage integrity check failed")
        return record


def reviews_for_sources(language, sources):
    records = read_reviews().get(language, {})
    if not isinstance(records, dict):
        return {}
    urls = {item.get("url") for item in sources if isinstance(item, dict)}
    return {url: record for url, record in records.items()
            if url in urls and isinstance(record, dict)}


def filter_audit_events(events, language, source="", status="", from_date="", to_date=""):
    """Filter audit events by UTC date and new status; reject invalid inputs."""
    from datetime import date
    if language not in ("DE", "FR", "IT", "RM"):
        raise ValueError("Invalid language")
    if status and status not in STATUSES:
        raise ValueError("Invalid status")
    if len(source) > 2048:
        raise ValueError("Invalid source")
    try:
        start = date.fromisoformat(from_date) if from_date else None
        end = date.fromisoformat(to_date) if to_date else None
    except (TypeError, ValueError):
        raise ValueError("Invalid date") from None
    if start and end and start > end:
        raise ValueError("Invalid date range")
    filtered = []
    for event in events:
        if not isinstance(event, dict) or event.get("language") != language:
            continue
        if source and event.get("url") != source:
            continue
        if status and event.get("new_status") != status:
            continue
        try:
            changed = datetime.fromisoformat(event["changed_at"].replace("Z", "+00:00"))
            if changed.tzinfo is None:
                continue
            day = changed.astimezone(timezone.utc).date()
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
        if (start and day < start) or (end and day > end):
            continue
        filtered.append(event)
    return filtered
