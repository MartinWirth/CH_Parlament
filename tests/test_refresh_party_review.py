"""Verify party-page candidates are included in normal refresh snapshots."""
import asyncio
import json

from app import main


def test_refresh_includes_official_party_review_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_profile_snapshot_path", tmp_path / "snapshot.json")
    monkeypatch.setattr(main, "save_profile_snapshot",
                        lambda snapshot: (tmp_path / "snapshot.json").write_text(
                            json.dumps(snapshot), encoding="utf-8"))
    async def members(_language):
        return [{"PersonNumber": 42, "FirstName": "Ada", "LastName": "Beispiel", "Active": True}]
    async def communications(_language, _ids):
        return [], {}
    async def homepage(_rows, _names):
        return {}, 0, {"checked": 0}
    async def party(_rows, _fresh):
        return {}, {"pages": [{
            "person_number": "42", "name": "Ada Beispiel",
            "profile_url": "https://party.example.org/ada",
            "rejected": {"X": {"url": "https://x.com/unconfirmed_handle"}}}]}
    monkeypatch.setattr(main, "fetch_members", members)
    monkeypatch.setattr(main, "fetch_communications", communications)
    monkeypatch.setattr(main, "homepage_profiles", homepage)
    monkeypatch.setattr(main, "party_profile_links", party)
    monkeypatch.setattr(main, "communication_profiles", lambda _: {})
    monkeypatch.setattr(main, "load_review_candidates",
                        lambda *_args, **_kwargs: ({}, {"configured": 0}))
    result = asyncio.run(main._run_social_profiles_refresh("DE", True))
    data = json.loads((tmp_path / "snapshot.json").read_text())["DE"]
    assert data["review_candidates"]["42"][0]["auto_accept"] is False
    assert data["review_candidates"]["42"][0]["source_type"] == "official_party_profile"
    assert "42" not in data["profiles"]
