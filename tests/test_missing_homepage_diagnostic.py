"""Focused regression coverage for the missing-homepage diagnostic."""
import asyncio

from app import main


def test_missing_homepage_diagnostic_uses_snapshot_without_scanning(monkeypatch):
    rows = [
        {"PersonNumber": 1, "Active": True, "name": "Person One"},
        {"PersonNumber": 2, "Active": True, "name": "Person Two"},
    ]
    communications = [
        {"PersonNumber": 1, "CommunicationTypeText": "Homepage", "Address": "https://one.example.org"},
        {"PersonNumber": 2, "CommunicationTypeText": "Homepage", "Address": "https://two.example.org"},
    ]

    async def fetch_members(language):
        return rows

    async def fetch_communications(language, ids):
        return communications, {"error": None}

    async def unexpected_scan(*args, **kwargs):
        raise AssertionError("Default diagnostic must not scan homepages")

    monkeypatch.setattr(main, "fetch_members", fetch_members)
    monkeypatch.setattr(main, "fetch_communications", fetch_communications)
    monkeypatch.setattr(main, "homepage_profiles", unexpected_scan)
    monkeypatch.setattr(main, "is_current", lambda row: row["Active"])
    monkeypatch.setattr(main, "normalize", lambda row: {"name": row["name"]})
    monkeypatch.setattr(main, "read_profile_snapshot", lambda: {
        "DE": {"updated_at": "2026-10-26", "profiles": {"2": {"X": "https://x.com/two"}}}
    })
    result = asyncio.run(main.missing_homepage_diagnostic(language="DE", scan=False))
    assert result["missing_homepage_count"] == 1
    assert result["cases"][0]["person_number"] == "1"
    assert result["scan_requested"] is False
