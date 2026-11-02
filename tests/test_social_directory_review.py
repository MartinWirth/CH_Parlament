import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from app import admin
from app.social_directory_review import update_review, read_reviews, reviews_for_sources


def test_review_persists_and_filters_by_language_and_source(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    url = "https://sg.die-mitte.ch/menschen/"
    result = update_review("DE", url, "in_review", "Bitte prüfen", [url])
    assert result["status"] == "in_review"
    assert read_reviews()["DE"][url]["comment"] == "Bitte prüfen"
    assert reviews_for_sources("DE", [{"url": url}])[url]["status"] == "in_review"
    assert reviews_for_sources("FR", [{"url": url}]) == {}
    assert reviews_for_sources("DE", [{"url": "https://other.example/"}]) == {}
    update_review("DE", url, "resolved", "Behoben", [url])
    assert read_reviews()["DE"][url]["status"] == "resolved"


@pytest.mark.parametrize("status,comment,urls", [
    ("invalid", "", ["https://sg.die-mitte.ch/menschen/"]),
    ("in_review", "x" * 1001, ["https://sg.die-mitte.ch/menschen/"]),
    ("in_review", "", []),
])
def test_invalid_review_rejected(tmp_path, monkeypatch, status, comment, urls):
    monkeypatch.setenv("SOCIAL_DIRECTORY_REVIEW_PATH", str(tmp_path / "reviews.json"))
    with pytest.raises(ValueError):
        update_review("DE", "https://sg.die-mitte.ch/menschen/", status, comment, urls)


def test_review_endpoint_requires_login():
    class Request:
        cookies = {}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.admin_directory_source_review(Request()))
    assert exc.value.status_code == 401


def test_review_endpoint_requires_same_origin():
    class Request:
        cookies = {}
    with patch.object(admin, "_require"), patch.object(admin, "_same_origin", return_value=False):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(admin.admin_directory_source_review(Request()))
    assert exc.value.status_code == 403
