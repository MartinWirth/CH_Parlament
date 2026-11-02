import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import admin
from app.social_directory_review import (
    update_review, storage_health, verify_and_recover, recovery_log_path,
    review_path, audit_path,
)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(tmp_path / "audit.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_PATH", str(tmp_path / "backup.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_RECOVERY_LOG_PATH", str(tmp_path / "recovery.json"))
    return "https://sg.die-mitte.ch/menschen/"


def test_empty_storage(configured):
    assert storage_health()["status"] == "empty"
    assert verify_and_recover()["status"] == "empty"


def test_healthy_after_write(configured):
    update_review("DE", configured, "in_review", "Check", [configured])
    assert storage_health()["status"] == "healthy"
    assert storage_health()["last_recovery"] is None


def test_corruption_is_repaired_and_logged(configured):
    update_review("DE", configured, "resolved", "Fixed", [configured])
    review_path().write_text("{broken")
    audit_path().write_text("[broken")
    assert storage_health()["status"] == "recovery_needed"
    assert verify_and_recover()["status"] == "healthy"
    assert recovery_log_path().exists()
    assert storage_health()["last_recovery"]["restored_events"] == 1
    assert verify_and_recover()["last_recovery"]["restored_events"] == 1


def test_invalid_backup_not_recovered(configured):
    review_path().write_text("{broken")
    assert verify_and_recover()["status"] == "degraded"
    assert not recovery_log_path().exists()


def test_health_endpoint_requires_login():
    class Request:
        cookies = {}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.admin_directory_storage_health(Request()))
    assert exc.value.status_code == 401


def test_health_endpoint_returns_status(configured):
    with patch.object(admin, "_require"):
        result = asyncio.run(admin.admin_directory_storage_health(object()))
    assert result["status"] == "empty"
