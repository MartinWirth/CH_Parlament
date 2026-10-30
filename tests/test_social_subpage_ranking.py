from app.social_subpage_ranking import rank_internal_pages


def test_multilingual_subpage_ranking_and_determinism():
    current = "https://party.example/person/ada"
    links = [
        {"href": "/person/ada/contatti", "text": "Contatti"},
        {"href": "/person/ada/reseaux-sociaux", "text": "Réseaux sociaux"},
        {"href": "/person/ada/biographie", "text": "Biographie"},
        {"href": "https://other.example/contact", "text": "Contact"},
    ]
    pages, count, rejected = rank_internal_pages(current, links)
    assert pages == [
        "https://party.example/person/ada/biographie",
        "https://party.example/person/ada/contatti",
    ]
    assert count == 3
    assert rejected == 1


def test_navigation_rejects_unsafe_urls_and_deduplicates():
    current = "https://party.example/person/ada"
    links = [
        {"href": "/contact", "text": "Contact"},
        {"href": "/contact", "text": "Contact"},
        {"href": "https://party.example/contact?token=1", "text": "Contact"},
        {"href": "https://user:pass@party.example/contact", "text": "Contact"},
        {"href": "http://party.example/contact", "text": "Contact"},
        {"href": "/random", "text": "News"},
    ]
    pages, count, rejected = rank_internal_pages(current, links)
    assert pages == ["https://party.example/contact"]
    assert count == 1
    assert rejected == 4
