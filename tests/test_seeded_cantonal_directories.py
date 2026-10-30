from pathlib import Path

from app.social_cantonal_directories import configured_cantonal_directories


def test_seeded_cantonal_directories_are_official_and_unique():
    path = Path(__file__).resolve().parents[1] / "social_cantonal_party_directories.json"
    urls = configured_cantonal_directories(path)
    assert len(urls) == 10
    assert len(urls) == len(set(urls))
    assert "https://sg.die-mitte.ch/menschen/" in urls
    assert "https://tg.die-mitte.ch/menschen/" in urls
    assert "https://gr.die-mitte.ch/menschen/" in urls

    for url in ("https://www.svp.ch/partei/kantone/zuerich/",
                "https://www.svp.ch/partei/kantone/bern/",
                "https://www.svp.ch/partei/kantone/aargau/",
                "https://www.fdp.ch/partei/organe/fraktion/",
                "https://www.sp-ps.ch/menschen/national-und-staenderat/",
                "https://gruene.ch/gruene-im-bundeshaus"):
        assert url in urls
