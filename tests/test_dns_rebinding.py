"""DNS rebinding regression tests for the connection-bound HTTPS resolver."""
import asyncio
import socket

import pytest
import httpx

from app.public_https import PublicOnlyResolver, PublicHTTPSClient


def _addr(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (ip, 443))


def test_resolver_accepts_public_addresses(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [_addr("8.8.8.8")])
    addresses = asyncio.run(PublicOnlyResolver().resolve("example.org", 443))
    assert addresses[0]["host"] == "8.8.8.8"
    assert addresses[0]["hostname"] == "example.org"


@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"],
    ["::1"], ["fc00::1"], ["8.8.8.8", "127.0.0.1"],
])
def test_resolver_rejects_private_or_mixed_dns_answers(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [_addr(ip) for ip in addresses])
    with pytest.raises(OSError, match="non-public"):
        asyncio.run(PublicOnlyResolver().resolve("example.org", 443))


def test_client_rejects_non_https_urls_before_network():
    async def run():
        async with PublicHTTPSClient() as client:
            for url in ("http://example.org/", "https://example.org:8443/"):
                with pytest.raises(httpx.ConnectError):
                    await client.get(url)
    asyncio.run(run())
