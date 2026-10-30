import asyncio
import json
from unittest.mock import patch

from app.social_cantonal_directories import discover_cantonal_directory_candidates


def test_directory_diagnostic_reports_failed_and_unproductive_sources(tmp_path):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps([
        {"url": "https://zh.glp.swiss/people/", "verified": True},
        {"url": "https://sg.die-mitte.ch/menschen/", "verified": True},
    ]), encoding="utf-8")

    async def fake_scan(client, url, semaphore, report):
        if "glp.swiss" in url:
            report["status"] = "error"
            report["error"] = "HTTP 404"
        else:
            report["status"] = "parsed"
            report["directory_links"] = []
        return {}

    with patch("app.main.scan_homepage", side_effect=fake_scan):
        found, diagnostic = asyncio.run(discover_cantonal_directory_candidates(
            {"1": "Example Person"}, lambda url: None, path=sources))

    assert found == {}
    assert diagnostic["configured"] == 2
    assert diagnostic["parsed_directories"] == 1
    assert diagnostic["sources"] == [
        {"url": "https://zh.glp.swiss/people/", "status": "error",
         "usable": False, "matched_members": 0, "reason": "HTTP 404"},
        {"url": "https://sg.die-mitte.ch/menschen/", "status": "parsed",
         "usable": True, "matched_members": 0,
         "reason": "no_matching_member_links"},
    ]
