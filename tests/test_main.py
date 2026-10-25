from app.main import normalize, is_current

def test_normalize():
    result=normalize({"PersonNumber":12,"FirstName":"Anna","LastName":"Muster","CouncilName":"Nationalrat","PartyAbbreviation":"SP"})
    assert result["name"]=="Anna Muster"
    assert result["partei"]=="SP"

def test_inactive():
    assert not is_current({"Active":False})
    assert is_current({"Active":True})
    assert not is_current({"DateLeaving":"2020-01-01"})


def test_social_media():
    from app.main import social_media
    assert social_media({"TwitterUrl": "https://x.com/example", "FacebookUrl": "javascript:alert(1)"}) == {"X": "https://x.com/example"}
    assert normalize({"FirstName": "Anna"})["socialmedia"] == {}


def test_personal_handle_matches():
    from app.main import personal_handle_matches
    assert personal_handle_matches("https://www.instagram.com/anna.muster/", "Anna Muster")
    assert personal_handle_matches("https://www.facebook.com/anna-muster", "Anna Muster")
    assert not personal_handle_matches("https://www.instagram.com/spschweiz/", "Anna Muster")
    assert not personal_handle_matches("https://example.org/anna-muster", "Anna Muster")
    assert not personal_handle_matches("http://instagram.com/anna-muster", "Anna Muster")


def test_fdp_roster_person_name_strips_office_titles():
    from app.main import fdp_roster_person_name
    assert fdp_roster_person_name("Cyril Aellen Nationalrat") == "Cyril Aellen"
    assert fdp_roster_person_name("Bettina Balmer Nationalrätin") == "Bettina Balmer"
    assert fdp_roster_person_name("Damien Cottier Ständerat") == "Damien Cottier"
    assert fdp_roster_person_name("  Marcel Dobler   Nationalrat  ") == "Marcel Dobler"
    assert fdp_roster_person_name("Andri Silberschmidt") == "Andri Silberschmidt"


def test_www_redirect_only_same_domain():
    from app.main import same_site_www_redirect
    assert same_site_www_redirect("example.ch", "www.example.ch")
    assert same_site_www_redirect("www.example.ch", "example.ch")
    assert same_site_www_redirect("example.ch", "example.ch")
    assert not same_site_www_redirect("example.ch", "evil-example.ch")
    assert not same_site_www_redirect("example.ch", "example.com")


def test_social_parser_twitter_creator_metadata():
    from app.main import SocialLinkParser
    parser = SocialLinkParser()
    parser.feed('<meta name="twitter:creator" content="@exampleperson">')
    assert "https://twitter.com/exampleperson" in parser.links


def test_homepage_redirect_normalization():
    from app.main import canonical_homepage_url, same_site_www_redirect
    assert canonical_homepage_url("https://www.barbaraschaffner.ch/") == "https://www.barbaraschaffner.ch/"
    assert canonical_homepage_url("https://barbaraschaffner.ch/") == "https://barbaraschaffner.ch/"
    assert same_site_www_redirect("www.barbaraschaffner.ch", "barbaraschaffner.ch")
    assert canonical_homepage_url("https://müller.ch/path") == "https://xn--mller-kva.ch/path"


def test_homepage_redirect_rejects_invalid_authority():
    import pytest
    from app.main import canonical_homepage_url
    for url in ("https://[not-ipv6]/", "https://user:pass@example.ch/", "http://example.ch/", "https://example.ch:444/"):
        with pytest.raises(ValueError):
            canonical_homepage_url(url)


def test_cross_domain_redirect_requires_explicit_approval(monkeypatch):
    from app.main import approved_homepage_redirect
    monkeypatch.delenv("APPROVED_HOMEPAGE_REDIRECTS", raising=False)
    assert not approved_homepage_redirect("example.ch", "evil.ch")
    monkeypatch.setenv("APPROVED_HOMEPAGE_REDIRECTS", '{"example.ch":"new-example.ch"}')
    assert approved_homepage_redirect("example.ch", "new-example.ch")
    assert not approved_homepage_redirect("example.ch", "new-example.ch.evil.org")


def test_party_personal_handle_swiss_transliteration():
    from app.main import party_personal_handle_matches
    assert party_personal_handle_matches("https://www.instagram.com/bmuehlemann/", "Benjamin Mühlemann")
    assert party_personal_handle_matches("https://x.com/Damian_Mueller_/", "Damian Müller")
    assert party_personal_handle_matches("https://www.facebook.com/damian.mueller/", "Damian Müller")
    assert party_personal_handle_matches("https://www.instagram.com/benjamin-m%C3%BChlemann/", "Benjamin Mühlemann")
    assert not party_personal_handle_matches("https://www.instagram.com/fdp_schweiz/", "Benjamin Mühlemann")
    assert not party_personal_handle_matches("https://www.facebook.com/fdp.dieliberalen/", "Damian Müller")
    assert not party_personal_handle_matches("https://www.instagram.com/other_person/", "Damian Müller")
