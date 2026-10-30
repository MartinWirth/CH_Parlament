import asyncio
from unittest.mock import patch

from app import admin


def test_directory_health_requires_authentication():
    class Request:
        cookies = {}
    try:
        asyncio.run(admin.admin_directory_source_health(Request()))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("Unauthenticated request must be rejected")


def test_directory_health_reads_saved_snapshot():
    saved = {"DE": {"updated_at": "2026-10-30T00:00:00Z", "debug": {
        "review_candidates": {"cantonal_directory_discovery": {
            "configured": 2, "parsed_directories": 1,
            "sources": [{"url": "https://sg.die-mitte.ch/menschen/",
                         "status": "parsed", "usable": True, "matched_members": 3}]
        }}}}}
    with patch.object(admin, "_require"), patch("app.main.read_profile_snapshot", return_value=saved):
        report = asyncio.run(admin.admin_directory_source_health(object()))
    assert report["configured"] == 2
    assert report["parsed_directories"] == 1
    assert report["sources"][0]["matched_members"] == 3
