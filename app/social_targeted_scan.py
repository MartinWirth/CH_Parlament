"""Bounded follow-up scans of parliament-listed homepages for uncovered members.

Only known HTTPS homepages are visited via the existing SSRF-safe scanner.
Links remain unverified review candidates, never automatically published.
"""
import asyncio
from urllib.parse import urlparse


def _safe_source(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "https" and bool(parsed.hostname) and
                not parsed.username and not parsed.password and
                parsed.port in (None, 443) and not parsed.fragment)
    except (ValueError, UnicodeError):
        return False


async def discover_uncovered_homepage_candidates(
        member_names, homepage_urls, existing_profiles, verified_social_url,
        *, checked_at, max_pages=30):
    from .main import scan_homepage
    from .public_https import PublicHTTPSClient
    names = member_names if isinstance(member_names, dict) else {}
    homepages = homepage_urls if isinstance(homepage_urls, dict) else {}
    profiles = existing_profiles if isinstance(existing_profiles, dict) else {}
    max_pages = max(0, min(int(max_pages), 30))
    jobs = [(str(number), url) for number, url in homepages.items()
            if str(number) in names and not profiles.get(str(number))
            and _safe_source(url)][:max_pages]
    diagnostic = {"eligible_scanned": len(jobs), "parsed": 0, "candidates": 0}
    if not jobs:
        return {}, diagnostic
    reports = [{} for _ in jobs]
    semaphore = asyncio.Semaphore(4)
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(
            scan_homepage(client, url, semaphore, reports[i])
            for i, (_, url) in enumerate(jobs)))
    found = {}
    for (number, source), links, report in zip(jobs, results, reports):
        if report.get("status") != "parsed":
            continue
        diagnostic["parsed"] += 1
        raw = (list(links.values()) + report.get("social_url_candidates", []) +
               report.get("raw_social_urls", []) + report.get("data_social_urls", []))
        seen = set()
        for url in raw[:100]:
            if not isinstance(url, str) or url in seen:
                continue
            seen.add(url)
            try:
                platform = verified_social_url(url)
            except (ValueError, UnicodeError):
                continue
            if platform:
                found.setdefault(number, []).append({
                    "platform": platform, "url": url,
                    "source_type": "targeted_parliament_listed_homepage",
                    "source_url": source, "checked_at": checked_at,
                    "verification_status": "requires_identity_verification",
                    "auto_accept": False,
                    "reason": "targeted_homepage_link_requires_manual_identity_confirmation",
                })
                diagnostic["candidates"] += 1
    return found, diagnostic
