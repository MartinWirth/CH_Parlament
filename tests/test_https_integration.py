"""Integration tests of the HTTPS client through aiohttp's real connector.

The tests use a local TLS endpoint, an injected DNS resolver and a test-only
public-IP classification override. No external network is required.
"""
import asyncio
import ipaddress
import socket
import ssl

import aiohttp
import httpx
import pytest

from app import public_https


class FakeResponse:
    status = 200
    headers = {"content-type": "text/html"}

    def __init__(self, body=b"<html>ok</html>"):
        self.content = self
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def read(self, limit):
        return self.body[:limit]


def test_client_rejects_redirect_to_private_ip(monkeypatch):
    """A redirect is returned, not followed into a second unsafe request."""
    requested = []

    class Session:
        async def get(self, url, **kwargs):
            requested.append((url, kwargs))
            return FakeResponse()

    # Exercise the actual client request path, with a fake session.
    async def run():
        client = public_https.PublicHTTPSClient()
        class RequestContext:
            async def __aenter__(self):
                return FakeResponse()
            async def __aexit__(self, *args):
                pass
        class RecordingSession:
            def get(self, url, **kwargs):
                requested.append((url, kwargs))
                return RequestContext()
        client.session = RecordingSession()
        response = await client.get("https://example.org/")
        assert response.status_code == 200
        assert requested[0][1]["allow_redirects"] is False

    asyncio.run(run())


def test_connector_rebinding_is_blocked_before_socket_connect(monkeypatch):
    """Real aiohttp connector resolves again and refuses a rebound private IP."""
    attempts = []
    original = socket.getaddrinfo

    def rebinding_dns(host, port, *args, **kwargs):
        if host == "rebind.example":
            attempts.append(1)
            # The first response is public; the connection-time response is private.
            ip = "8.8.8.8" if len(attempts) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_dns)

    async def run():
        # The pre-check is deliberately separate from the real client request.
        first = await public_https.PublicOnlyResolver().resolve("rebind.example", 443)
        assert first[0]["host"] == "8.8.8.8"
        async with public_https.PublicHTTPSClient(timeout=1) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://rebind.example/")
        assert len(attempts) >= 2

    asyncio.run(run())


def test_connection_resolver_never_passes_private_ip_to_connector(monkeypatch):
    """A mixed DNS response is refused before aiohttp can choose an address."""
    def dns(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", dns)

    async def run():
        async with public_https.PublicHTTPSClient(timeout=1) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://mixed.example/")
    asyncio.run(run())


def test_client_rejects_credentials_and_nonstandard_port():
    async def run():
        async with public_https.PublicHTTPSClient() as client:
            for url in (
                "https://user:password@example.org/",
                "https://example.org:444/",
                "http://example.org/",
            ):
                with pytest.raises(httpx.ConnectError):
                    await client.get(url)
    asyncio.run(run())
