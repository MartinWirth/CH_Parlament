"""HTTPS client with DNS validation at the actual connection resolver.

aiohttp's connector connects to the IP addresses returned by this resolver while
retaining the original hostname for TLS certificate verification and SNI.
"""
import asyncio
import ipaddress
import socket

import aiohttp
import httpx


class PublicOnlyResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        # Resolve for each connection, reject the *entire* answer set if any
        # address is private, loopback, link-local, reserved or otherwise non-global.
        answers = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(item[4][0]).is_global for item in answers):
            raise OSError("DNS resolution rejected: non-public address")
        return [
            {"hostname": host, "host": item[4][0], "port": port,
             "family": item[0], "proto": item[2], "flags": 0}
            for item in answers
        ]

    async def close(self):
        pass


class PublicHTTPSClient:
    """Minimal httpx-compatible interface for the homepage scanner."""

    def __init__(self, timeout=8, headers=None):
        self.timeout = timeout
        self.headers = headers or {}
        self.session = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(
            resolver=PublicOnlyResolver(), use_dns_cache=False,
            force_close=True, ssl=True)
        self.session = aiohttp.ClientSession(
            connector=connector, trust_env=False, headers=self.headers)
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def get(self, url, **kwargs):
        parsed = httpx.URL(url)
        if (parsed.scheme != "https" or parsed.port not in (None, 443)
                or parsed.userinfo or not parsed.host):
            raise httpx.ConnectError("Only public HTTPS endpoints are permitted")
        timeout = kwargs.get("timeout", self.timeout)
        try:
            async with self.session.get(
                url, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                # Bound the actual download, not just the later HTML parser.
                content = await response.content.read(3_000_001)
                return httpx.Response(
                    response.status, headers=dict(response.headers), content=content,
                    request=httpx.Request("GET", url))
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise httpx.ReadTimeout(str(exc)) from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise httpx.ConnectError(str(exc)) from exc
