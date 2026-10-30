"""OData pagination and duplicate-handling regression tests."""
import asyncio
import httpx
import pytest
from app import main


class FakeClient:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        self.calls.append((str(url), params))
        payload = next(self.pages)
        request = httpx.Request("GET", str(url))
        return httpx.Response(200, json=payload, request=request)


def test_member_pagination_deduplicates(monkeypatch):
    client = FakeClient([
        {"d": {"results": [{"ID": 1, "Language": "DE"}], "__next": "https://api.test/next"}},
        {"d": {"results": [{"ID": 1, "Language": "DE"}, {"ID": 2, "Language": "DE"}]}}
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: client)
    rows = asyncio.run(main.fetch_members())
    assert [row["ID"] for row in rows] == [1, 2]
    assert client.calls[1][0] == "https://api.test/next"


def test_member_rejects_invalid_page(monkeypatch):
    client = FakeClient([{"d": {"results": {"ID": 1}}}])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(ValueError, match="page structure"):
        asyncio.run(main.fetch_members())


def test_communication_duplicate_ids_are_skipped(monkeypatch):
    client = FakeClient([{"d": {"results": [
        {"ID": 10, "PersonNumber": 1, "Language": "DE", "Address": "https://example.org"},
        {"ID": 10, "PersonNumber": 1, "Language": "DE", "Address": "https://example.org"}
    ]}}])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: client)
    rows, diagnostic = asyncio.run(main.fetch_communications("DE", {1}))
    assert len(rows) == 1
    assert diagnostic["duplicates"] == 1
    assert diagnostic["error"] is None
