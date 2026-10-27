import json

from app.main import verified_social_url
from app.social_evidence import evidence_for_sources, load_review_candidates


def test_evidence_retains_platform_source_and_check_time():
    records = evidence_for_sources(
        {"42": {"Parlamentarier-Homepage": {"Instagram": "https://instagram.com/example"},
                "Partei-Homepage": {"LinkedIn": "https://linkedin.com/in/example"}}},
        {"42": "https://example.ch"},
        "2026-10-27T00:00:00+00:00")
    assert len(records["42"]) == 2
    assert records["42"][0]["source_url"] == "https://example.ch"
    assert records["42"][0]["verification_status"] == "source_attributed"
    assert records["42"][1]["source_type"] == "Partei-Homepage"
    assert records["42"][1]["source_url"] is None
    assert all(item["checked_at"] == "2026-10-27T00:00:00+00:00"
               for item in records["42"])


def test_review_candidates_require_exact_identity_and_source(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps([
        {"person_number": "42", "name": "Ada Beispiel",
         "url": "https://instagram.com/ada_beispiel",
         "source_url": "https://party.example.ch/ada"},
        {"person_number": "42", "name": "Other Person",
         "url": "https://x.com/other", "source_url": "https://example.ch"},
        {"person_number": "42", "name": "Ada Beispiel",
         "url": "http://instagram.com/ada", "source_url": "https://example.ch"},
        {"person_number": "42", "name": "Ada Beispiel",
         "url": "https://facebook.com/ada", "source_url": "http://example.ch"},
        {"person_number": "99", "name": "Ada Beispiel",
         "url": "https://x.com/ada", "source_url": "https://example.ch"},
    ]), encoding="utf-8")
    found, stats = load_review_candidates(
        {"42": "Ada Beispiel"}, verified_social_url, path,
        "2026-10-27T00:00:00+00:00")
    assert len(found["42"]) == 1
    assert found["42"][0]["platform"] == "Instagram"
    assert found["42"][0]["auto_accept"] is False
    assert found["42"][0]["verification_status"] == "requires_identity_verification"
    assert stats["accepted_for_review"] == 1
    assert stats["rejected"] == 4


def test_missing_candidate_file_is_safe(tmp_path):
    found, stats = load_review_candidates(
        {"42": "Ada Beispiel"}, verified_social_url,
        tmp_path / "absent.json")
    assert found == {}
    assert stats["accepted_for_review"] == 0
