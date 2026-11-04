"""API-level backup/restore and cross-process maintenance locking regression tests."""
import multiprocessing
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import admin
from app.main import app
from app import social_directory_maintenance as maintenance
from app.social_directory_review import read_reviews, update_review


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH", str(tmp_path / "backup.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_VERSIONS_DIR", str(tmp_path / "versions"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_INTERVAL_HOURS", "0")
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_RETENTION_COUNT", "3")
    return "https://sg.die-mitte.ch/menschen/"


def test_admin_backup_restore_end_to_end(configured, monkeypatch):
    monkeypatch.setattr(admin, "_require", lambda request: None)
    url = configured
    update_review("DE", url, "resolved", "first", [url])
    assert maintenance.run_once()["status"] == "healthy"
    with TestClient(app) as client:
        response = client.get("/admin/directory-backup-versions")
        assert response.status_code == 200
        version = response.json()["versions"][0]["id"]
        update_review("DE", url, "resolved", "second", [url])
        assert client.post("/admin/directory-backup-version-restore",
                           json={"version": version}).status_code == 403
        response = client.post(
            "/admin/directory-backup-version-restore",
            json={"version": version},
            headers={"Origin": "http://testserver", "X-Admin-Action": "1"})
        assert response.status_code == 200
        assert response.json()["ok"] is True
    assert read_reviews()["DE"][url]["comment"] == "first"
    assert maintenance.backup_path().with_name(
        maintenance.backup_path().name + ".pre-restore").exists()


def _other_process_lock(path, ready, release):
    os.environ["SOCIAL_DIRECTORY_DB_PATH"] = path
    from app.social_directory_maintenance import process_lock
    with process_lock():
        ready.set()
        release.wait(8)


def test_process_lock_serializes_maintenance(configured):
    update_review("DE", configured, "resolved", "first", [configured])
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_other_process_lock,
                          args=(os.environ["SOCIAL_DIRECTORY_DB_PATH"], ready, release))
    process.start()
    try:
        assert ready.wait(6)
        import threading
        finished = threading.Event()
        results = []

        def backup():
            results.append(maintenance.run_once())
            finished.set()

        worker = threading.Thread(target=backup)
        worker.start()
        assert not finished.wait(0.3), "Backup must wait for another process"
        release.set()
        assert finished.wait(8)
        worker.join(timeout=2)
        assert results[0]["status"] == "healthy"
        assert maintenance.list_versions()
    finally:
        release.set()
        process.join(timeout=8)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0


def test_admin_manual_backup_integrity_check(configured, monkeypatch):
    monkeypatch.setattr(admin, "_require", lambda request: None)
    update_review("DE", configured, "resolved", "saved", [configured])
    maintenance.run_once()
    with TestClient(app) as client:
        assert client.post("/admin/directory-backup-verify").status_code == 403
        response = client.post("/admin/directory-backup-verify", headers={
            "Origin": "http://testserver", "X-Admin-Action": "1"})
        assert response.status_code == 200
        assert response.json()["valid"] == 1
        assert response.json()["versions"][0]["created_at"]


def test_admin_isolated_restore_drill(configured, monkeypatch):
    monkeypatch.setattr(admin, "_require", lambda request: None)
    update_review("DE", configured, "resolved", "backup", [configured])
    assert maintenance.run_once()["status"] == "healthy"
    update_review("DE", configured, "resolved", "current", [configured])
    with TestClient(app) as client:
        assert client.post("/admin/directory-backup-restore-test").status_code == 403
        response = client.post("/admin/directory-backup-restore-test", headers={
            "Origin": "http://testserver", "X-Admin-Action": "1"})
        assert response.status_code == 200
        assert response.json()["restorable"] == 1
        assert response.json()["versions"][0]["events"] == 1
    assert read_reviews()["DE"][configured]["comment"] == "current"
