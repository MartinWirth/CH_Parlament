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


def test_evp_verified_public_name_alias():
    from app.main import evp_member_candidates
    members = {
        "niklaussamuelgugger": [("123", "Niklaus-Samuel Gugger")],
        "marcjost": [("456", "Marc Jost")],
    }
    assert evp_member_candidates("nikgugger", members) == [("123", "Niklaus-Samuel Gugger")]
    assert evp_member_candidates("marcjost", members) == [("456", "Marc Jost")]
    assert evp_member_candidates("nikgugger", {"other": [("999", "Other")]}) == []
    assert evp_member_candidates("unknownperson", members) == []


def test_prioritize_missing_homepages():
    from app.main import prioritize_missing_homepages
    details = [
        {"name": "No links", "reason": "no_social_links", "fetch_status": "parsed"},
        {"name": "Candidates", "reason": "no_verified_personal_link", "fetch_status": "parsed"},
        {"name": "Timeout", "reason": "fetch_error", "fetch_status": "timeout"},
        {"name": "Redirect", "reason": "fetch_error", "fetch_status": "redirect_blocked"},
    ]
    ordered = prioritize_missing_homepages(details)
    assert [row["name"] for row in ordered] == ["Redirect", "Timeout", "Candidates", "No links"]
    assert [row["name"] for row in details] == ["No links", "Candidates", "Timeout", "Redirect"]


def test_verified_personal_site_redirects(monkeypatch):
    from app.main import approved_homepage_redirect
    monkeypatch.delenv("APPROVED_HOMEPAGE_REDIRECTS", raising=False)
    for source, target in (
        ("andrisilberschmidt.ch", "silberschmidt2027.ch"),
        ("marc-jost.ch", "marcjost.ch"),
        ("susanne-vincenz-stauffacher.ch", "vincenz-stauffacher.ch"),
    ):
        assert approved_homepage_redirect(source, target)
        assert approved_homepage_redirect("www." + source, "www." + target)
        assert not approved_homepage_redirect(source, "attacker.example")
        assert not approved_homepage_redirect("fake." + source, target)
    assert not approved_homepage_redirect("unknown.example", "marcjost.ch")


def test_bounded_homepage_html():
    from app.main import bounded_homepage_html
    assert bounded_homepage_html(b"a" * 100) == b"a" * 100
    diagnostic = {}
    result = bounded_homepage_html(b"b" * 1_500_000, diagnostic)
    assert len(result) == 1_000_000
    assert diagnostic["html_truncated_for_parsing"] is True
    assert diagnostic["html_bytes"] == 1_500_000
    diagnostic = {}
    assert bounded_homepage_html(b"c" * 3_000_001, diagnostic) is None
    assert diagnostic["status"] == "html_too_large"


def test_homepage_error_categories():
    import httpx
    from app.main import homepage_error_category

    assert homepage_error_category(httpx.ConnectError("certificate verify failed")) == "tls_certificate"
    assert homepage_error_category(httpx.ConnectError("getaddrinfo failed")) == "dns_resolution"
    assert homepage_error_category(httpx.ConnectError("connection refused")) == "tcp_connection"
    assert homepage_error_category(httpx.ReadTimeout("read timed out")) == "timeout"
    assert homepage_error_category(httpx.ConnectTimeout("connect timed out")) == "timeout"
    assert homepage_error_category(httpx.ReadError("connection reset")) == "http_transport"
    assert homepage_error_category(ValueError("invalid url")) == "invalid_url_or_data"


def test_jauslin_verified_redirect_only():
    from app.main import approved_homepage_redirect
    assert approved_homepage_redirect("www.jauslin.com", "www.mjauslin.ch")
    assert approved_homepage_redirect("jauslin.com", "mjauslin.ch")
    assert not approved_homepage_redirect("jauslin.com", "unverified.example")
    assert not approved_homepage_redirect("fake.jauslin.com", "mjauslin.ch")


def test_tls_handshake_error_is_classified_and_unverified_redirect_rejected():
    import httpx
    from app.main import homepage_error_category, approved_homepage_redirect

    assert homepage_error_category(httpx.ConnectError("TLS handshake failed")) == "tls_certificate"
    assert homepage_error_category(httpx.ConnectError("SSL handshake failure")) == "tls_certificate"
    assert not approved_homepage_redirect("mauro-tuena.ch", "unverified.example")
    assert not approved_homepage_redirect("reginesauter.ch", "unverified.example")
    assert not approved_homepage_redirect("andreasmichaelmeier.ch", "unverified.example")


def test_andreas_meier_verified_domain_only():
    from app.main import approved_homepage_redirect

    assert approved_homepage_redirect("andreasmichaelmeier.ch", "andreasmeier.net")
    assert approved_homepage_redirect("www.andreasmichaelmeier.ch", "www.andreasmeier.net")
    assert not approved_homepage_redirect("andreasmichaelmeier.ch", "unverified.example")
    assert not approved_homepage_redirect("fake.andreasmichaelmeier.ch", "andreasmeier.net")
