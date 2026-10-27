"""Run an authenticated social-profile refresh locally, without exposing a token to browsers.

Usage:
    SOCIAL_REFRESH_TOKEN=... python -m app.refresh_cli --language DE
The command invokes the FastAPI route in-process, so the same authentication,
concurrency and validation checks apply. Run it on the deployment host.
"""
import argparse
import asyncio
import os
import sys

import httpx

from .main import app


async def run(language, force):
    token = os.environ.get("SOCIAL_REFRESH_TOKEN")
    if not token:
        raise RuntimeError("SOCIAL_REFRESH_TOKEN must be configured on the server")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.post(
            "/api/social-profiles/refresh",
            params={"language": language, "force": str(force).lower()},
            headers={"X-Refresh-Token": token},
            timeout=None,
        )
        response.raise_for_status()
        result = response.json()
        print("Refresh completed:", result.get("updated_at", "no timestamp"))


def main():
    parser = argparse.ArgumentParser(description="Authenticated server-side profile refresh")
    parser.add_argument("--language", choices=["DE", "FR", "IT", "RM"], default="DE")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.language, args.force))
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"Refresh failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
