import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import admin
from app.social_directory_review import (
    update_review, read_reviews, read_audit, backup_path,
    create_backup, restore_backup,
)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_AUDIT_PATH", str(tmp_path / "audit.json"))
    monkeypatch.setenv("SOCIAL_DIRECTORY_BACKUP_PATH", str(tmp_path / "backup.json"))
    return "https://sg.die-mitte.ch/menschen/"


def test_parallel_writes_preserve_every_event(storage):
    def write(index):
        return update_review("DE", storage, "in_review", str(index), [storage])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    assert len(read_audit()) == 40
    assert len(json.loads(backup_path().read_text())["audit"]) == 40


def test_corrupt_primary_files_recovered_from_backup(storage, tmp_path):
    update_review("DE", storage, "resolved", "Restorable", [storage])
    (tmp_path / "reviews.json").write_text("{broken")
    (tmp_path / "audit.json").write_text("[broken")
    assert read_reviews()["DE"][storage]["comment"] == "Restorable"
    assert len(read_audit()) == 1
    assert restore_backup()["events"] == 1
    assert json.loads((tmp_path / "reviews.json").read_text())["DE"][storage]["status"] == "resolved"


def test_explicit_backup(storage):
    update_review("DE", storage, "in_review", "Saved", [storage])
    assert create_backup()["events"] == 1


def test_invalid_backup_cannot_be_restored(storage):
    backup_path().write_text('{"reviews": []}')
    with pytest.raises(ValueError):
        restore_backup()


def test_backup_endpoint_requires_authentication():
    class Request:
        cookies = {}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.admin_directory_review_backup(Request()))
    assert exc.value.status_code == 401


def test_restore_endpoint_requires_same_origin():
    with patch.object(admin, "_require"), patch.object(admin, "_same_origin", return_value=False):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(admin.admin_directory_review_restore(object()))
    assert exc.value.status_code == 403
