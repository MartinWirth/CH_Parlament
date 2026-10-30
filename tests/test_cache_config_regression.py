"""Regression tests for cache bounds and environment defaults."""
from pathlib import Path
import httpx
from app import main


def test_environment_example_has_unique_keys():
    env = Path(__file__).resolve().parents[1] / ".env.example"
    keys = [line.split("=", 1)[0] for line in env.read_text().splitlines() if line and not line.lstrip().startswith("#") and "=" in line]
    assert len(keys) == len(set(keys))


def test_http_cache_respects_aggregate_limit(monkeypatch):
    monkeypatch.setattr(main, "_http_response_cache_max_entries", 20)
    monkeypatch.setattr(main, "_http_response_cache_max_bytes", 100)
    monkeypatch.setattr(main, "_http_response_cache_total_max_bytes", 120)
    main._http_response_cache.clear()
    try:
        for i in range(4):
            url = f"https://example.org/{i}"
            response = httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 50, request=httpx.Request("GET", url))
            main.store_http_response(url, response)
        assert sum(len(entry[3]) for entry in main._http_response_cache.values()) <= 120
        assert len(main._http_response_cache) <= 2
    finally:
        main._http_response_cache.clear()
