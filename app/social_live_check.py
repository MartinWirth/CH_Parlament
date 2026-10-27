"""Bounded, opt-in social profile reachability checks; never mutate profiles."""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urljoin

from .public_https import PublicHTTPSClient
from .social_quality import canonical_url


def classify_status(status):
    if status in (404, 410):
        return "possibly_missing"
    if status in (401, 403, 429):
        return "restricted_or_rate_limited"
    if 200 <= status < 300:
        return "reachable"
    if 300 <= status < 400:
        return "redirect"
    return "uncertain"


async def check_links(profiles, verified_social_url, *, limit=100, client_factory=PublicHTTPSClient):
    """Check a bounded set of distinct public HTTPS URLs, without following redirects.

    A 403/429 is not evidence of a deleted profile. Return redirect targets
    for review but never fetch them, avoiding unsafe cross-host redirects.
    """
    links, seen = [], set()
    for number, platforms in (profiles if isinstance(profiles, dict) else {}).items():
        if not isinstance(platforms, dict):
            continue
        for platform, url in platforms.items():
            canonical = canonical_url(url)
            try:
                valid = bool(canonical and verified_social_url(url) == platform)
            except (ValueError, UnicodeError, TypeError):
                valid = False
            if not valid or canonical in seen:
                continue
            seen.add(canonical)
            links.append((str(number), str(platform), url))
            if len(links) >= limit:
                break
        if len(links) >= limit:
            break
    semaphore = asyncio.Semaphore(3)

    async def inspect(number, platform, url):
        async with semaphore:
            result = {"person_number": number, "platform": platform, "url": url,
                      "checked_at": datetime.now(timezone.utc).isoformat()}
            try:
                async with client_factory(timeout=6, headers={"User-Agent": "CH-Parlament-LinkAudit/1.0"}) as client:
                    response = await client.get(url, timeout=6)
                result["http_status"] = response.status_code
                result["status"] = classify_status(response.status_code)
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location", "")
                    target = urljoin(url, location)
                    # Do not expose unsafe redirect targets as clickable links.
                    if canonical_url(target) and verified_social_url(target):
                        result["redirect_target"] = target
            except Exception:
                result["status"] = "request_failed"
            return result

    results = await asyncio.gather(*(inspect(*link) for link in links))
    return {"checked": len(results), "truncated": len(seen) >= limit,
            "results": results, "checked_at": datetime.now(timezone.utc).isoformat()}
