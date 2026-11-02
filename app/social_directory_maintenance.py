"""Periodic SQLite integrity checks and verified backups for directory reviews."""
import json
import logging
import os
import sqlite3
import tempfile
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import social_directory_sqlite as store

logger = logging.getLogger(__name__)
_rotation_lock = threading.RLock()
_VERSION_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\\.sqlite3$")


def interval_hours():
    raw = os.getenv("SOCIAL_DIRECTORY_MAINTENANCE_INTERVAL_HOURS", "0")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("Invalid directory maintenance interval") from None
    if not 0 <= value <= 8760:
        raise ValueError("Directory maintenance interval must be between 0 and 8760 hours")
    return value


def backup_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH",
                          str(store.database_path()) + ".backup"))


def metadata_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_MAINTENANCE_STATE_PATH",
                          str(store.database_path()) + ".maintenance.json"))


def _read_metadata():
    try:
        data = json.loads(metadata_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_metadata(data):
    target = metadata_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=target.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _valid_backup(path):
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
            return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and all(
                db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (table,)).fetchone() for table in ("reviews", "audit"))
    except (sqlite3.Error, OSError):
        return False



def retention_count():
    try:
        count = int(os.getenv("SOCIAL_DIRECTORY_BACKUP_RETENTION_COUNT", "7"))
    except ValueError:
        raise ValueError("Invalid backup retention count") from None
    if not 1 <= count <= 365:
        raise ValueError("Backup retention count must be between 1 and 365")
    return count


def versions_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_BACKUP_VERSIONS_DIR",
                          str(backup_path()) + ".versions"))


def list_versions():
    """Return only safe, validated backup versions, newest first."""
    directory = versions_path()
    if not directory.is_dir():
        return []
    versions = []
    for item in sorted(directory.iterdir(), reverse=True):
        if not _VERSION_RE.fullmatch(item.name) or item.is_symlink() or not item.is_file():
            continue
        versions.append({"id": item.name, "valid": _valid_backup(item),
                         "size_bytes": item.stat().st_size})
    return versions


def _archive_backup(source):
    """Publish an immutable snapshot, then prune only after success."""
    if not _valid_backup(source):
        raise ValueError("Cannot archive invalid SQLite backup")
    directory = versions_path()
    directory.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12] + ".sqlite3"
    target = directory / name
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            temp = Path(handle.name)
        with sqlite3.connect(str(source)) as original, sqlite3.connect(str(temp)) as snapshot:
            original.backup(snapshot)
        if not _valid_backup(temp):
            raise ValueError("Archived SQLite backup failed validation")
        os.replace(temp, target)
        for item in list_versions()[retention_count():]:
            (directory / item["id"]).unlink()
        return name
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def restore_version(version_id):
    """Restore a selected, validated version; preserve the current DB first."""
    if not isinstance(version_id, str) or not _VERSION_RE.fullmatch(version_id):
        raise ValueError("Invalid backup version ID")
    with _rotation_lock:
        path = versions_path() / version_id
        if path.is_symlink() or not _valid_backup(path):
            raise ValueError("Backup version not found or invalid")
        # Keep a safety snapshot of the current state before destructive restore.
        if store.database_path().exists():
            current = backup_path().with_name(backup_path().name + ".pre-restore")
            store.backup_to(current)
            if not _valid_backup(current):
                raise ValueError("Pre-restore safety backup failed")
        from .social_directory_sqlite import restore_from
        reviews, audit = restore_from(path)
        return {"reviews": len(reviews), "events": len(audit), "version": version_id}

def status():
    data = _read_metadata()
    path = backup_path()
    return {"enabled": interval_hours() > 0,
            "interval_hours": interval_hours(),
            "last_check_at": data.get("last_check_at"),
            "last_check_status": data.get("last_check_status"),
            "last_backup_at": data.get("last_backup_at"),
            "last_error": data.get("last_error"),
            "backup_valid": _valid_backup(path),
            "versions": len(list_versions()), "retention_count": retention_count()}


def run_once():
    """Check integrity, then create a verified replacement without destroying prior backup."""
    if not store.database_path().exists():
        return {"status": "empty", **status()}
    state = _read_metadata()
    state["last_check_at"] = datetime.now(timezone.utc).isoformat()
    temp = None
    try:
        with store.connection() as db:
            good = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            if not good:
                raise ValueError("SQLite integrity_check failed")
            state["last_check_status"] = "healthy"
            destination = backup_path()
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
                temp = Path(handle.name)
            with sqlite3.connect(str(temp)) as target:
                db.backup(target)
        if not _valid_backup(temp):
            raise ValueError("Generated SQLite backup failed validation")
        with _rotation_lock:
            os.replace(temp, destination)
            _archive_backup(destination)
        state["last_backup_at"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = None
        _write_metadata(state)
        return {"status": "healthy", **status()}
    except (OSError, sqlite3.Error, ValueError) as exc:
        state["last_check_status"] = "degraded"
        state["last_error"] = str(exc)
        _write_metadata(state)
        logger.error("Directory SQLite maintenance failed: %s", exc)
        return {"status": "degraded", **status()}
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


async def periodic_loop():
    import asyncio
    hours = interval_hours()
    if hours <= 0:
        return
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            logger.exception("Unexpected directory SQLite maintenance error")
        await asyncio.sleep(hours * 3600)
