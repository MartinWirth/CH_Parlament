"""Periodic SQLite integrity checks and verified backups for directory reviews."""
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import social_directory_sqlite as store

logger = logging.getLogger(__name__)


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


def status():
    data = _read_metadata()
    path = backup_path()
    return {"enabled": interval_hours() > 0,
            "interval_hours": interval_hours(),
            "last_check_at": data.get("last_check_at"),
            "last_check_status": data.get("last_check_status"),
            "last_backup_at": data.get("last_backup_at"),
            "last_error": data.get("last_error"),
            "backup_valid": _valid_backup(path)}


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
        os.replace(temp, destination)
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
