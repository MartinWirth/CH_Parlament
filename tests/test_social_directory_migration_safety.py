import json
import sqlite3

import pytest

from app import social_directory_sqlite as store
from app.social_directory_review import (
    audit_path, backup_path, migrate_legacy_to_sqlite, migration_status,
    read_audit, read_reviews, review_path, storage_health,
)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_PATH", str(tmp_path / "review.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(tmp_path / "audit.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_PATH", str(tmp_path / "checkpoint.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MIGRATION_BACKUP_DIR", str(tmp_path / "safety"))
    url = "https://sg.die-mitte.ch/menschen/"
    reviews = {"DE": {url: {"status": "resolved", "comment": "ok",
                             "reviewed_at": "2026-01-01"}}}
    audit = [{"language": "DE", "url": url, "changed_at": "2026-01-01",
              "previous_status": None, "new_status": "resolved",
              "previous_comment": None, "comment": "ok"}]
    review_path().write_text(json.dumps(reviews))
    audit_path().write_text(json.dumps(audit))
    backup_path().write_text(json.dumps({"reviews": reviews, "audit": audit}))
    return tmp_path, url


def test_migration_preserves_original_files_and_records_status(configured):
    path, url = configured
    before = [p.read_bytes() for p in (review_path(), audit_path(), backup_path())]
    assert migrate_legacy_to_sqlite() is True
    assert migration_status()["state"] == "completed"
    assert migration_status()["reviews"] == 1
    assert storage_health()["migration"]["events"] == 1
    for name, original in zip(("reviews.json", "audit.json", "checkpoint.json"), before):
        assert (path / "safety" / name).read_bytes() == original
    assert migrate_legacy_to_sqlite() is False
    assert read_reviews()["DE"][url]["comment"] == "ok"


def test_invalid_legacy_data_fails_without_import(configured):
    path, url = configured
    backup_path().write_text(json.dumps({"reviews": {"DE": {url: {
        "status": "invalid", "comment": "bad", "reviewed_at": "2026-01-01"}}},
        "audit": []}))
    with pytest.raises(ValueError):
        migrate_legacy_to_sqlite()
    assert migration_status()["state"] == "not_migrated"
    assert not (path / "safety" / "checkpoint.json").exists()


def test_interrupted_import_rolls_back_atomically_and_can_retry(configured):
    path, url = configured
    with store.connection() as db:
        db.execute("""CREATE TRIGGER block_audit BEFORE INSERT ON audit
                      BEGIN SELECT RAISE(ABORT, 'simulated crash'); END""")
        db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        migrate_legacy_to_sqlite()
    assert read_reviews() == {}
    assert read_audit() == []
    assert migration_status()["state"] == "not_migrated"
    assert (path / "safety" / "checkpoint.json").exists()
    with store.connection() as db:
        db.execute("DROP TRIGGER block_audit")
        db.commit()
    assert migrate_legacy_to_sqlite() is True
    assert read_reviews()["DE"][url]["status"] == "resolved"


def test_backup_conflict_blocks_migration(configured):
    path, url = configured
    (path / "safety").mkdir()
    (path / "safety" / "reviews.json").write_text("different")
    with pytest.raises(ValueError):
        migrate_legacy_to_sqlite()
    assert migration_status()["state"] == "not_migrated"
