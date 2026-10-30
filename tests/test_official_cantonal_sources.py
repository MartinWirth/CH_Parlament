import json

from app.social_regional_discovery import configured_regional_pages, _official_host


def test_multilingual_party_domains_are_supported():
    for host in ("section.plr.ch", "udc.ch", "lesverts.ch", "le-centre.ch", "pssuisse.ch"):
        assert _official_host(host)
    assert not _official_host("fakeplr.ch")
    assert not _official_host("plr.ch.attacker.example")


def test_curated_pages_require_verified_exact_person_and_official_https(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([
        {"person_number": "12", "name": "Test Person",
         "profile_url": "https://section.plr.ch/person/test", "verified": True},
        {"person_number": "13", "name": "Other Person",
         "profile_url": "https://udc.ch/person/test", "verified": False},
        {"person_number": "14", "name": "Wrong Name",
         "profile_url": "https://lesverts.ch/person/test", "verified": True},
        {"person_number": "15", "name": "Another Person",
         "profile_url": "https://plr.ch.attacker.example/person/test", "verified": True},
    ]))
    pages, report = configured_regional_pages(
        {"12": "Test Person", "13": "Other Person",
         "14": "Correct Name", "15": "Another Person"}, path)
    assert pages == {"12": "https://section.plr.ch/person/test"}
    assert report["rejected"] == 3
