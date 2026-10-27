"""Real TLS socket integration tests; no external network is contacted."""
import asyncio
import shutil
import socket
import ssl
import subprocess

import aiohttp
from aiohttp import web
import httpx
import pytest

from app.public_https import PublicHTTPSClient
from app import refresh_cli


class LocalResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, port):
        self.port = port
        self.calls = []

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.calls.append(host)
        return [{"hostname": host, "host": "127.0.0.1", "port": self.port,
                 "family": socket.AF_INET, "proto": socket.IPPROTO_TCP, "flags": 0}]

    async def close(self):
        pass


def test_real_tls_connection_and_redirect_not_followed(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl executable unavailable")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=example.test",
        "-addext", "subjectAltName=DNS:example.test",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def run():
        requests = []
        async def home(request):
            requests.append("home")
            return web.Response(text="<html>secure</html>", content_type="text/html")
        async def redirect(request):
            requests.append("redirect")
            raise web.HTTPFound("https://127.0.0.1/admin")
        app = web.Application()
        app.router.add_get("/", home)
        app.router.add_get("/redirect", redirect)
        runner = web.AppRunner(app)
        await runner.setup()
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(str(cert), str(key))
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_tls)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        resolver = LocalResolver(port)
        client_tls = ssl.create_default_context(cafile=str(cert))
        try:
            async with PublicHTTPSClient(_resolver=resolver, _ssl_context=client_tls) as client:
                response = await client.get("https://example.test/")
                assert response.status_code == 200
                assert response.content == b"<html>secure</html>"
                redirect_response = await client.get("https://example.test/redirect")
                assert redirect_response.status_code == 302
                assert redirect_response.headers["location"] == "https://127.0.0.1/admin"
                assert requests == ["home", "redirect"]
                assert resolver.calls == ["example.test", "example.test"]
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_real_tls_rejects_untrusted_certificate(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl executable unavailable")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=example.test", "-addext", "subjectAltName=DNS:example.test",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def run():
        app = web.Application()
        app.router.add_get("/", lambda request: web.Response(text="ok"))
        runner = web.AppRunner(app)
        await runner.setup()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert), str(key))
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with PublicHTTPSClient(_resolver=LocalResolver(port)) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get("https://example.test/")
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_server_side_cli_uses_authenticated_post(monkeypatch, capsys):
    monkeypatch.setenv("SOCIAL_REFRESH_TOKEN", "test-secret")
    calls = []
    async def fake_refresh(language, force):
        calls.append((language, force))
        return {"updated_at": "2026-10-27"}
    from app import main
    monkeypatch.setattr(main, "_run_social_profiles_refresh", fake_refresh)
    asyncio.run(refresh_cli.run("DE", True))
    assert calls == [("DE", True)]
    assert "Refresh completed" in capsys.readouterr().out


def test_server_side_cli_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("SOCIAL_REFRESH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="SOCIAL_REFRESH_TOKEN"):
        asyncio.run(refresh_cli.run("DE", False))
