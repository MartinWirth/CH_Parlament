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


def configured_regional_pages(member_names, path=None, *, existing_profiles=None):
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
    # Prioritize members with no confirmed profiles while retaining coverage
    # of members with partial profiles and their other platforms.
    existing_profiles = existing_profiles if isinstance(existing_profiles, dict) else {}
    result = dict(sorted(result.items(), key=lambda pair: (
        bool(existing_profiles.get(pair[0])), pair[0])))
    return result, {"configured": len(entries), "accepted": len(result),
                    "rejected": rejected + max(0, len(entries) - 200)}


def configured_additional_party_pages(member_names, path=None, *, existing_profiles=None):
    """Optional explicitly verified same-host person subpages for uncovered members.

    A source entry may list up to three additional_profile_urls. No URL guessing.
    """
    path = Path(path or os.getenv("SOCIAL_REGIONAL_PARTY_SOURCES_PATH",
                                 "social_regional_party_sources.json"))
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    profiles = existing_profiles if isinstance(existing_profiles, dict) else {}
    jobs, seen = [], set()
    for entry in entries[:200]:
        if not isinstance(entry, dict) or entry.get("verified") is not True:
            continue
        number = str(entry.get("person_number", ""))
        name = entry.get("name")
        source = entry.get("profile_url")
        if (not isinstance(name, str) or
                name.strip().casefold() != member_names.get(number, "").strip().casefold()
                or profiles.get(number) or not isinstance(source, str)):
            continue
        try:
            parent = urlparse(source)
            if (parent.scheme != "https" or not _official_host(parent.hostname)
                    or parent.username or parent.password or parent.port not in (None, 443)):
                continue
        except (ValueError, UnicodeError):
            continue
        urls = entry.get("additional_profile_urls", [])
        if not isinstance(urls, list):
            continue
        for url in urls[:3]:
            if not isinstance(url, str):
                continue
            try:
                child = urlparse(url)
                valid = (child.scheme == "https" and child.hostname == parent.hostname
                         and not child.username and not child.password
                         and child.port in (None, 443) and bool(child.path.strip("/"))
                         and not child.query and not child.fragment and url != source)
            except (ValueError, UnicodeError):
                valid = False
            if valid and (number, url) not in seen:
                seen.add((number, url))
                jobs.append((number, url))
    return jobs[:100]


async def discover_regional_party_candidates(member_names, verified_social_url, *, path=None,
                                              checked_at=None, existing_profiles=None):
    """Inspect at most 200 curated official person pages; require manual review."""
    from .main import scan_homepage
    from .public_https import PublicHTTPSClient
    from .social_evidence import _utc_now
    checked_at = checked_at or _utc_now()
    pages, diagnostic = configured_regional_pages(
        member_names, path, existing_profiles=existing_profiles)
    additional_jobs = configured_additional_party_pages(\n        member_names, path, existing_profiles=existing_profiles)\n    if not pages and not additional_jobs:\n        return {}, diagnostic
    semaphore = asyncio.Semaphore(4)
    reports = [{} for _ in pages]
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(
            scan_homepage(client, url, semaphore, reports[index])
            for index, url in enumerate(pages.values())))
    # Inspect up to two relevant same-host subpages of each curated person page.
    # The shared scanner enforces HTTPS, public destinations and redirect safety.
    subpage_jobs = list(additional_jobs)
    for (number, source), report in zip(pages.items(), reports):
        if report.get("status") != "parsed":
            continue
        for suburl in report.get("internal_page_candidates", [])[:2]:
            if not isinstance(suburl, str):
                continue
            try:
                parent, child = urlparse(source), urlparse(suburl)
                allowed = (child.scheme == "https" and child.hostname == parent.hostname
                           and not child.username and not child.password
                           and child.port in (None, 443) and not child.query
                           and not child.fragment and child.path != parent.path)
            except (ValueError, UnicodeError):
                allowed = False
            if allowed:
                subpage_jobs.append((number, suburl))
    subpage_reports = [{} for _ in subpage_jobs]
    if subpage_jobs:
        async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
            subpage_links = await asyncio.gather(*(
                scan_homepage(client, url, semaphore, subpage_reports[index])
                for index, (_, url) in enumerate(subpage_jobs)))
    else:
        subpage_links = []
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
    for (number, source), links, report in zip(subpage_jobs, subpage_links, subpage_reports):
        if report.get("status") != "parsed":
            continue
        candidates = (list(links.values()) + report.get("social_url_candidates", [])
                      + report.get("raw_social_urls", []) + report.get("data_social_urls", []))
        seen = set()
        for url in candidates[:100]:
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
                    "source_type": "curated_regional_party_person_page",
                    "source_url": source, "checked_at": checked_at,
                    "verification_status": "requires_identity_verification",
                    "auto_accept": False,
                    "reason": "official_subpage_link_requires_manual_identity_confirmation",
                })
    diagnostic["explicit_additional_pages"] = len(additional_jobs)\n    diagnostic["subpages_scanned"] = len(subpage_jobs)
    diagnostic["subpages_parsed"] = sum(
        report.get("status") == "parsed" for report in subpage_reports)
    diagnostic["candidates"] = sum(len(items) for items in found.values())
    diagnostic["scanned"] = len(pages)
    diagnostic["scanned_without_confirmed_profiles"] = sum(
        not bool((existing_profiles or {}).get(number)) for number in pages)
    return found, diagnostic
