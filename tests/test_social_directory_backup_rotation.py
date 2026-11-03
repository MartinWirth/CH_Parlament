import pytest
from app import social_directory_maintenance as maintenance
from app.social_directory_review import update_review, read_reviews


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_PATH", str(tmp_path / "reviews.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_DB_BACKUP_PATH", str(tmp_path / "backup.sqlite3"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_VERSIONS_DIR", str(tmp_path / "versions"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_MAINTENANCE_STATE_PATH", str(tmp_path / "maintenance.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_RETENTION_COUNT", "2")
    url = "https://sg.die-mitte.ch/menschen/"
    return url


def test_backup_rotation_and_restore(configured):
    url = configured
    for label in ("one", "two", "three"):
        update_review("DE", url, "resolved", label, [url])
        assert maintenance.run_once()["status"] == "healthy"
    versions = maintenance.list_versions()
    assert len(versions) == 2
    assert all(item["valid"] for item in versions)
    assert maintenance.status()["retention_count"] == 2
    # Restore the older of the two retained versions.
    assert maintenance.restore_version(versions[-1]["id"])["reviews"] == 1
    assert read_reviews()["DE"][url]["comment"] == "two"


def test_reject_invalid_version_without_touching_data(configured):
    url = configured
    update_review("DE", url, "resolved", "original", [url])
    maintenance.run_once()
    for version in ("../backup.sqlite3", "unknown.sqlite3", "", "x" * 200):
        with pytest.raises(ValueError):
            maintenance.restore_version(version)
    assert read_reviews()["DE"][url]["comment"] == "original"


def test_invalid_backup_is_not_restorable(configured):
    url = configured
    update_review("DE", url, "resolved", "original", [url])
    maintenance.run_once()
    version = maintenance.list_versions()[0]["id"]
    (maintenance.versions_path() / version).write_bytes(b"corrupt")
    assert maintenance.list_versions()[0]["valid"] is False
    with pytest.raises(ValueError):
        maintenance.restore_version(version)


@pytest.mark.parametrize("value", ["0", "-2", "bad", "366"])
def test_invalid_retention(monkeypatch, value):
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_RETENTION_COUNT", value)
    with pytest.raises(ValueError):
        maintenance.retention_count()


def test_backup_history_timestamp_and_manual_verification(configured):
    from datetime import datetime
    update_review("DE", configured, "resolved", "saved", [configured])
    assert maintenance.run_once()["status"] == "healthy"
    versions = maintenance.list_versions()
    assert len(versions) == 1
    assert datetime.fromisoformat(versions[0]["created_at"]).tzinfo is not None
    assert versions[0]["size_bytes"] > 0
    result = maintenance.verify_versions()
    assert result["checked"] == 1
    assert result["valid"] == 1
    assert result["invalid"] == 0
    (maintenance.versions_path() / versions[0]["id"]).write_bytes(b"corrupt")
    result = maintenance.verify_versions()
    assert result["invalid"] == 1
    assert result["versions"][0]["valid"] is False
