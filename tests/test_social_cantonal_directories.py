import json
from app.social_cantonal_directories import (
    configured_cantonal_directories, match_directory_links)


def test_only_verified_official_directory_sources(tmp_path):
    path = tmp_path / "directories.json"
    path.write_text(json.dumps([
        {"url": "https://zh.glp.swiss/partei/menschen/mandatstraeger-innen/", "verified": True},
        {"url": "https://evil.example/members/", "verified": True},
        {"url": "https://zh.glp.swiss/other/", "verified": False}
    ]), encoding="utf-8")
    assert configured_cantonal_directories(path) == [
        "https://zh.glp.swiss/partei/menschen/mandatstraeger-innen/"]


def test_directory_matches_only_exact_unique_name_slug():
    source = "https://zh.glp.swiss/partei/menschen/mandatstraeger-innen/"
    links = [
        "https://zh.glp.swiss/personen/martin-baeumle/",
        "https://zh.glp.swiss/personen/martin-baeumle/",
        "https://other.example/personen/patrick-haessig/",
        "https://zh.glp.swiss/personen/patrick-haessig/?redirect=1",
        "https://zh.glp.swiss/personen/tiana-angelina-moser/"
    ]
    found = match_directory_links(source, links, {
        "1": "Martin Bäumle", "2": "Patrick Hässig",
        "3": "Tiana Angelina Moser"}, {"3": {"LinkedIn": "confirmed"}})
    assert found == {"1": "https://zh.glp.swiss/personen/martin-baeumle/"}
