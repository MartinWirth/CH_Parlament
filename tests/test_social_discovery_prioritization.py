import json

from app.social_regional_discovery import configured_regional_pages


def test_members_without_confirmed_profiles_are_prioritized(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([
        {"person_number": "2", "name": "Two", "profile_url": "https://fdp.ch/two", "verified": True},
        {"person_number": "1", "name": "One", "profile_url": "https://svp.ch/one", "verified": True},
        {"person_number": "3", "name": "Three", "profile_url": "https://plr.ch/three", "verified": True},
    ]))
    pages, info = configured_regional_pages(
        {"1": "One", "2": "Two", "3": "Three"}, path,
        existing_profiles={"2": {"LinkedIn": "https://linkedin.com/in/two"}})
    assert list(pages) == ["1", "3", "2"]
    assert info["accepted"] == 3


def test_without_coverage_retains_deterministic_order(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([
        {"person_number": "9", "name": "Nine", "profile_url": "https://svp.ch/nine", "verified": True},
        {"person_number": "1", "name": "One", "profile_url": "https://fdp.ch/one", "verified": True},
    ]))
    pages, _ = configured_regional_pages({"1": "One", "9": "Nine"}, path)
    assert list(pages) == ["1", "9"]
