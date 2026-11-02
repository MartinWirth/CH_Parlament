import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import admin
from app.social_directory_review import filter_audit_events


EVENTS = [
    {"language": "DE", "url": "https://example.ch/a", "changed_at": "2026-10-01T23:30:00+00:00",
     "previous_status": None, "new_status": "in_review", "comment": "=SUM(1,1)"},
    {"language": "DE", "url": "https://example.ch/b", "changed_at": "2026-10-03T00:30:00+02:00",
     "previous_status": "in_review", "new_status": "resolved", "comment": "Done"},
    {"language": "FR", "url": "https://example.ch/a", "changed_at": "2026-10-01T00:00:00+00:00",
     "new_status": "deferred", "comment": "FR"},
]


def test_filter_by_utc_dates_status_and_source():
    assert len(filter_audit_events(EVENTS, "DE")) == 2
    assert len(filter_audit_events(EVENTS, "DE", from_date="2026-10-02")) == 1
    assert len(filter_audit_events(EVENTS, "DE", to_date="2026-10-01")) == 1
    assert len(filter_audit_events(EVENTS, "DE", status="resolved")) == 1
    assert len(filter_audit_events(EVENTS, "DE", source="https://example.ch/a")) == 1
    assert len(filter_audit_events(EVENTS, "FR")) == 1


@pytest.mark.parametrize("options", [
    {"language": "XX"}, {"language": "DE", "status": "invalid"},
    {"language": "DE", "from_date": "yesterday"},
    {"language": "DE", "from_date": "2026-11-02", "to_date": "2026-10-01"},
])
def test_invalid_filters(options):
    with pytest.raises(ValueError):
        filter_audit_events(EVENTS, **options)


def test_export_requires_authentication():
    class Request:
        cookies = {}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.admin_directory_review_audit(Request()))
    assert exc.value.status_code == 401


def test_csv_export_escapes_spreadsheet_formulas():
    with patch.object(admin, "_require"), patch("app.social_directory_review.read_audit", return_value=EVENTS):
        response = asyncio.run(admin.admin_directory_review_audit(object(), language="DE", format="csv"))
    body = response.body.decode("utf-8-sig")
    assert "'=SUM(1,1)" in body
    assert "https://example.ch/a" in body


def test_json_export_returns_filtered_events():
    with patch.object(admin, "_require"), patch("app.social_directory_review.read_audit", return_value=EVENTS):
        result = asyncio.run(admin.admin_directory_review_audit(object(), language="DE", status="resolved"))
    assert result["count"] == 1
    assert result["events"][0]["new_status"] == "resolved"
