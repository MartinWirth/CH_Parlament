import sqlite3

import pytest

from app import social_directory_maintenance as maintenance
from app.social_directory_review import update_review, storage_health


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH", str(tmp_path / "backup.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_STATE_PATH", str(tmp_path / "maintenance.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_INTERVAL_HOURS", "12")
    url = "https://sg.die-mitte.ch/menschen/"
    update_review("DE", url, "resolved", "verified", [url])
    return tmp_path, url


def test_maintenance_creates_verified_backup_and_exposes_status(configured):
    path, url = configured
    result = maintenance.run_once()
    assert result["status"] == "healthy"
    assert result["backup_valid"] is True
    assert result["last_check_status"] == "healthy"
    assert result["last_backup_at"]
    health = storage_health()
    assert health["backup_valid"] is True
    assert health["maintenance"]["interval_hours"] == 12
    with sqlite3.connect(str(path / "backup.sqlite3")) as db:
        assert db.execute("SELECT comment FROM reviews").fetchone()[0] == "verified"


def test_backup_is_not_overwritten_if_integrity_check_fails(configured, monkeypatch):
    assert maintenance.run_once()["status"] == "healthy"
    backup = maintenance.backup_path()
    original = backup.read_bytes()
    original_connection = maintenance.store.connection

    from contextlib import contextmanager

    @contextmanager
    def broken_connection():
        with original_connection() as db:
            class Proxy:
                def execute(self, sql, *args):
                    if sql == "PRAGMA integrity_check":
                        class Result:
                            def fetchone(self):
                                return ("corrupt",)
                        return Result()
                    return db.execute(sql, *args)
            yield Proxy()

    monkeypatch.setattr(maintenance.store, "connection", broken_connection)
    result = maintenance.run_once()
    assert result["status"] == "degraded"
    assert result["last_backup_at"]
    assert backup.read_bytes() == original


def test_empty_database_does_not_create_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_PATH", str(tmp_path / "missing.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH", str(tmp_path / "missing.backup"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_INTERVAL_HOURS", "0")
    assert maintenance.run_once()["status"] == "empty"
    assert not maintenance.backup_path().exists()


@pytest.mark.parametrize("value", ["bad", "-1", "8761"])
def test_invalid_interval_rejected(monkeypatch, value):
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_INTERVAL_HOURS", value)
    with pytest.raises(ValueError):
        maintenance.interval_hours()
