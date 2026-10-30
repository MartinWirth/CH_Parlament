from app.social_cantonal_directories import match_directory_links


def test_directory_matches_exact_anchor_label_with_nonstandard_url():
    source = "https://zh.glp.swiss/mitglieder/"
    links = [
        {"url": "https://zh.glp.swiss/p/12345", "text": "Martin Bäumle"},
        {"url": "https://zh.glp.swiss/p/45678", "text": "Patrick Hässig"},
        {"url": "https://zh.glp.swiss/p/77777", "text": "Martin Bäumle – Kontakt"},
        {"url": "https://outside.example/p/55555", "text": "Martin Bäumle"},
    ]
    assert match_directory_links(source, links, {"1": "Martin Bäumle", "2": "Patrick Hässig"}) == {
        "1": "https://zh.glp.swiss/p/12345",
        "2": "https://zh.glp.swiss/p/45678",
    }


def test_directory_rejects_conflicting_and_ambiguous_names():
    source = "https://zh.glp.swiss/mitglieder/"
    links = [
        {"url": "https://zh.glp.swiss/personen/martin-baeumle/", "text": "Patrick Hässig"},
        {"url": "https://zh.glp.swiss/p/12345", "text": "Martin Bäumle"},
        {"url": "https://zh.glp.swiss/p/12346", "text": "Martin Bäumle"},
    ]
    assert match_directory_links(source, links, {
        "1": "Martin Bäumle", "2": "Martin Bäumle", "3": "Patrick Hässig"}) == {}


def test_directory_rejects_query_fragment_and_credentials():
    source = "https://zh.glp.swiss/mitglieder/"
    links = [
        {"url": "https://zh.glp.swiss/p/123?x=1", "text": "Martin Bäumle"},
        {"url": "https://zh.glp.swiss/p/123#section", "text": "Martin Bäumle"},
        {"url": "https://user@zh.glp.swiss/p/123", "text": "Martin Bäumle"},
    ]
    assert match_directory_links(source, links, {"1": "Martin Bäumle"}) == {}
