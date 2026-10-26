"""Regression tests for the complete party snapshot fast path."""
import asyncio

from app import main


def test_complete_party_snapshot_skips_discovery(monkeypatch):
    rows = [{"PersonNumber": 1, "Active": True, "DateLeaving": None}]
    # Fail immediately if the fast path accidentally performs roster discovery.
    async def unexpected(*args, **kwargs):
        raise AssertionError("Party roster discovery must be skipped")

    monkeypatch.setattr(main, "discover_sp_party_profiles", unexpected)
    monkeypatch.setattr(main, "discover_other_party_profiles", unexpected)
    monkeypatch.setattr(main, "configured_party_profiles", lambda *args: (_ for _ in ()).throw(AssertionError("configuration lookup must be skipped")))
    monkeypatch.setattr(main, "is_current", lambda row: row["Active"])
    result, diagnostic = asyncio.run(main.party_profile_links(rows, {"1": {}}))
    assert result == {"1": {}}
    assert diagnostic["snapshot_reused_members"] == 1
    assert diagnostic["snapshot_scanned_members"] == 0
    assert diagnostic["discovery_skipped"] is True


def test_partial_party_snapshot_still_discovers(monkeypatch):
    rows = [{"PersonNumber": 1, "Active": True}, {"PersonNumber": 2, "Active": True}]
    calls = []
    monkeypatch.setattr(main, "is_current", lambda row: row["Active"])
    monkeypatch.setattr(main, "configured_party_profiles", lambda rows: ({}, {"configured": 0}))

    async def sp(rows):
        calls.append("sp")
        return {}, {"status": "empty"}

    async def other(rows):
        calls.append("other")
        return {}, {}

    monkeypatch.setattr(main, "discover_sp_party_profiles", sp)
    monkeypatch.setattr(main, "discover_other_party_profiles", other)
    monkeypatch.setattr(main, "normalize", lambda row: {"name": "Test Member"})
    result, diagnostic = asyncio.run(main.party_profile_links(rows, {"1": {}}))
    assert calls == ["sp", "other"]
    assert diagnostic["snapshot_scanned_members"] == 0
    assert result == {}
