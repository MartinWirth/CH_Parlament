import json

from app.social_regional_discovery import configured_additional_party_pages


def test_verified_additional_pages_only_for_uncovered_members(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([
        {"person_number": "1", "name": "Ada Example", "verified": True,
         "profile_url": "https://zh.glp.swiss/personen/ada/",
         "additional_profile_urls": [
             "https://zh.glp.swiss/personen/ada/kontakt/",
             "https://evil.example/personen/ada/",
             "http://zh.glp.swiss/personen/ada/about/",
             "https://zh.glp.swiss/personen/ada/kontakt/"]},
        {"person_number": "2", "name": "Other Example", "verified": True,
         "profile_url": "https://zh.glp.swiss/personen/other/",
         "additional_profile_urls": ["https://zh.glp.swiss/personen/other/kontakt/"]},
        {"person_number": "3", "name": "Wrong Name", "verified": True,
         "profile_url": "https://zh.glp.swiss/personen/three/",
         "additional_profile_urls": ["https://zh.glp.swiss/personen/three/kontakt/"]},
    ]), encoding="utf-8")
    jobs = configured_additional_party_pages(
        {"1": "Ada Example", "2": "Other Example", "3": "Actual Name"},
        path, existing_profiles={"2": {"LinkedIn": "confirmed"}})
    assert jobs == [("1", "https://zh.glp.swiss/personen/ada/kontakt/")]


def test_unverified_party_source_never_scanned(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([{
        "person_number": "1", "name": "Ada Example", "verified": False,
        "profile_url": "https://zh.glp.swiss/personen/ada/",
        "additional_profile_urls": ["https://zh.glp.swiss/personen/ada/kontakt/"]
    }]), encoding="utf-8")
    assert configured_additional_party_pages({"1": "Ada Example"}, path) == []
