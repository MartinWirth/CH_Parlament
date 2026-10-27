"""Identity-bearing Facebook profile.php query parameters must not be lost."""
from app.social_quality import canonical_url, audit_profiles
from app.social_check_history import compare_reports
from app.social_alert_triage import key_parts


def test_facebook_profile_id_is_preserved_and_tracking_removed():
    first = 'https://www.facebook.com/profile.php?id=123&utm_source=share'
    second = 'https://www.facebook.com/profile.php?utm_medium=social&id=456'
    assert canonical_url(first) == 'https://www.facebook.com/profile.php?id=123'
    assert canonical_url(second) == 'https://www.facebook.com/profile.php?id=456'
    assert canonical_url(first) != canonical_url(second)
    assert canonical_url('https://www.facebook.com/profile.php?id=123&id=456') is None
    assert canonical_url('https://www.facebook.com/profile.php?utm_source=test') is None
    assert canonical_url('https://www.facebook.com/profile.php?id=') is None
    assert canonical_url('https://www.facebook.com/person?ref=share') == 'https://www.facebook.com/person'


def test_duplicate_audit_does_not_conflate_distinct_facebook_accounts():
    def verifier(url):
        return 'Facebook'
    profiles = {'1': {'Facebook': 'https://www.facebook.com/profile.php?id=123'},
                '2': {'Facebook': 'https://www.facebook.com/profile.php?id=456'},
                '3': {'Facebook': 'https://www.facebook.com/profile.php?utm_source=share&id=123'}}
    result = audit_profiles(profiles, verifier)
    assert result['duplicates'] == 2
    assert {v['person_number'] for v in result['issues']} == {'1', '3'}


def test_check_comparison_and_triage_keys_are_account_specific():
    a = 'https://www.facebook.com/profile.php?id=123'
    b = 'https://www.facebook.com/profile.php?id=456'
    before = {'results': [{'person_number': '42', 'platform': 'Facebook', 'url': a,
                           'status': 'reachable', 'http_status': 200}]}
    after = {'results': [{'person_number': '42', 'platform': 'Facebook', 'url': b,
                          'status': 'possibly_missing', 'http_status': 404}]}
    assert compare_reports(before, after) == []
    assert key_parts('DE', '42', 'Facebook', a) != key_parts('DE', '42', 'Facebook', b)
