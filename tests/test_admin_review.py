import asyncio
import json

import httpx
from app import admin, main
from app.main import app


def test_admin_review_requires_auth_and_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_REFRESH_PASSWORD", "secret")
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "token")
    monkeypatch.setenv("SOCIAL_REVIEW_DECISIONS_PATH", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(main, "_profile_snapshot_path", tmp_path / "snapshot.json")
    main.save_profile_snapshot({"DE": {
        "profiles": {}, "profiles_by_source": {},
        "review_candidates": {"42": [{
            "url": "https://instagram.com/ada_example",
            "platform": "Instagram",
            "source_url": "https://party.example.org/ada",
            "auto_accept": False,
        }]}}})
    admin._sessions.clear()
    headers = {"Origin": "https://example.test", "X-Admin-Action": "1"}
    body = {"language": "DE", "person_number": "42",
            "url": "https://instagram.com/ada_example", "decision": "approved"}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            assert (await client.get("/admin/review")).status_code == 401
            assert (await client.post("/admin/review", json=body, headers=headers)).status_code == 401
            login = await client.post("/admin/login", json={"password": "secret"}, headers=headers)
            assert login.status_code == 200
            assert (await client.post("/admin/review", json=body)).status_code == 403
            assert (await client.post("/admin/review", json={**body, "url": "https://x.com/unknown"}, headers=headers)).status_code == 404
            result = await client.post("/admin/review", json=body, headers=headers)
            assert result.status_code == 200, result.text
            review = (await client.get("/admin/review")).json()
            assert review["candidates"]["42"][0]["review_status"] == "approved"
            snapshot = main.read_profile_snapshot()["DE"]
            assert snapshot["profiles"]["42"]["Instagram"] == body["url"]
            assert snapshot["profile_evidence"]["42"][0]["verification_status"] == "manually_approved"
    asyncio.run(run())


def test_rejected_candidate_is_not_promoted(monkeypatch, tmp_path):
    from app.social_review import decide
    monkeypatch.setenv("SOCIAL_REVIEW_DECISIONS_PATH", str(tmp_path / "decisions.json"))
    snapshot = {"DE": {"review_candidates": {"42": [{
        "url": "https://x.com/unverified", "platform": "X"}]}, "profiles": {}}}
    decide(snapshot, "DE", "42", "https://x.com/unverified", "rejected")
    assert snapshot["DE"]["profiles"] == {}
    assert snapshot["DE"]["review_candidates"]["42"][0]["review_status"] == "rejected"
