"""Conservative discovery from explicitly curated official regional party person pages.

No URL guessing or automatic identity attribution. Network fetching is delegated
to the existing SSRF-safe homepage scanner.
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

PARTY_DOMAINS = ("svp.ch", "fdp.ch", "sp-ps.ch", "die-mitte.ch",
                 "gruene.ch", "glp.swiss", "evppev.ch",
                 "pssuisse.ch", "lesverts.ch", "plr.ch", "le-centre.ch",
                 "udc.ch", "vertliberaux.ch")


def _official_host(host):
    return bool(host) and any(host == domain or host.endswith("." + domain)
                              for domain in PARTY_DOMAINS)


def configured_regional_pages(member_names, path=None):
    path = Path(path or os.getenv("SOCIAL_REGIONAL_PARTY_SOURCES_PATH",
                                 "social_regional_party_sources.json"))
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {"configured": 0, "accepted": 0, "rejected": 0}
    if not isinstance(entries, list):
        return {}, {"configured": 0, "accepted": 0, "rejected": 1}
    result, rejected = {}, 0
    for item in entries[:200]:
        if not isinstance(item, dict):
            rejected += 1
            continue
        number, name, url = str(item.get("person_number", "")), item.get("name"), item.get("profile_url")
        if (item.get("verified") is not True or not isinstance(name, str) or
                name.strip().casefold() != member_names.get(number, "").strip().casefold() or
                not isinstance(url, str) or number in result):
            rejected += 1
            continue
        try:
            parsed = urlparse(url)
            valid = (parsed.scheme == "https" and _official_host(parsed.hostname) and
                     not parsed.username and not parsed.password and parsed.port in (None, 443)
                     and bool(parsed.path.strip("/")))
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            rejected += 1
            continue
        result[number] = url
    return result, {"configured": len(entries), "accepted": len(result),
                    "rejected": rejected + max(0, len(entries) - 200)}


async def discover_regional_party_candidates(member_names, verified_social_url, *, path=None,
                                              checked_at=None):
    """Inspect at most 200 curated official person pages; require manual review."""
    from .main import scan_homepage
    from .public_https import PublicHTTPSClient
    from .social_evidence import _utc_now
    checked_at = checked_at or _utc_now()
    pages, diagnostic = configured_regional_pages(member_names, path)
    if not pages:
        return {}, diagnostic
    semaphore = asyncio.Semaphore(4)
    reports = [{} for _ in pages]
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(
            scan_homepage(client, url, semaphore, reports[index])
            for index, url in enumerate(pages.values())))
    found = {}
    for (number, source), links, report in zip(pages.items(), results, reports):
        if report.get("status") != "parsed":
            continue
        candidates = list(links.values()) + report.get("social_url_candidates", []) + report.get("raw_social_urls", []) + report.get("data_social_urls", [])
        seen = set()
        for url in candidates[:100]:
            if not isinstance(url, str) or url in seen:
                continue
            seen.add(url)
            try:
                platform = verified_social_url(url)
            except (ValueError, UnicodeError):
                continue
            if not platform:
                continue
            found.setdefault(number, []).append({
                "platform": platform, "url": url,
                "source_type": "curated_regional_party_person_page",
                "source_url": source, "checked_at": checked_at,
                "verification_status": "requires_identity_verification",
                "auto_accept": False,
                "reason": "official_page_link_requires_manual_identity_confirmation",
            })
    diagnostic["candidates"] = sum(len(items) for items in found.values())
    diagnostic["scanned"] = len(pages)
    return found, diagnostic
