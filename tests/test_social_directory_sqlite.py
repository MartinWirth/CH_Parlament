from concurrent.futures import ThreadPoolExecutor

import pytest

from app.social_directory_review import (
    update_review, read_reviews, read_audit, create_backup, restore_backup,
    storage_health, migrate_legacy_to_sqlite,
)


@pytest.fixture
def sqlite_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH", str(tmp_path / "backup.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "old.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(tmp_path / "old-audit.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_PATH", str(tmp_path / "old-backup.json"))
    return "https://sg.die-mitte.ch/menschen/"


def test_transactional_parallel_updates(sqlite_storage):
    def write(index):
        update_review("DE", sqlite_storage, "in_review", str(index), [sqlite_storage])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(35)))
    assert len(read_audit()) == 35
    assert read_reviews()["DE"][sqlite_storage]["status"] == "in_review"
    assert storage_health()["status"] == "healthy"


def test_sqlite_backup_and_restore(sqlite_storage):
    update_review("DE", sqlite_storage, "in_review", "saved", [sqlite_storage])
    assert create_backup()["events"] == 1
    update_review("DE", sqlite_storage, "resolved", "changed", [sqlite_storage])
    assert restore_backup()["events"] == 1
    assert read_reviews()["DE"][sqlite_storage]["comment"] == "saved"


def test_legacy_import_is_idempotent(sqlite_storage):
    import json
    from app.social_directory_review import review_path, audit_path
    review_path().write_text(json.dumps({"DE": {sqlite_storage: {
        "status": "resolved", "comment": "legacy", "reviewed_at": "2026-01-01"}}}))
    audit_path().write_text(json.dumps([{
        "language": "DE", "url": sqlite_storage, "changed_at": "2026-01-01",
        "previous_status": None, "new_status": "resolved",
        "previous_comment": None, "comment": "legacy"}]))
    assert migrate_legacy_to_sqlite() is True
    assert migrate_legacy_to_sqlite() is False
    assert read_reviews()["DE"][sqlite_storage]["comment"] == "legacy"
    assert len(read_audit()) == 1
