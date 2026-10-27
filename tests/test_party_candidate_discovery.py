from app.social_evidence import discover_party_review_candidates
from app.main import verified_social_url


def test_party_links_remain_unverified():
    pages = {'pages': [{'person_number': '42', 'name': 'Ada Beispiel', 'profile_url': 'https://party.example.org/ada', 'rejected': {'X': {'url': 'https://x.com/other_person'}}}]}
    found = discover_party_review_candidates(pages, {'42': 'Ada Beispiel'}, verified_social_url)
    assert len(found['42']) == 1
    assert found['42'][0]['auto_accept'] is False
    assert found['42'][0]['source_type'] == 'official_party_profile'
