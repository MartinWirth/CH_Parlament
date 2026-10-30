"""Conservative candidate discovery from verified cantonal party directories."""
import asyncio
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, unquote

from .social_regional_discovery import _official_host


def _slug(value):
    normalized = unicodedata.normalize("NFKD", value.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def configured_cantonal_directories(path=None):
    path = Path(path or os.getenv("SOCIAL_CANTONAL_DIRECTORY_SOURCES_PATH",
                                 "social_cantonal_party_directories.json"))
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    accepted = []
    for entry in entries[:30]:
        if not isinstance(entry, dict) or entry.get("verified") is not True:
            continue
        url = entry.get("url")
        if not isinstance(url, str):
            continue
        try:
            p = urlparse(url)
            if (p.scheme == "https" and _official_host(p.hostname)
                    and not p.username and not p.password and p.port in (None, 443)
                    and not p.query and not p.fragment and p.path.strip("/")):
                accepted.append(url)
        except (ValueError, UnicodeError):
            continue
    return list(dict.fromkeys(accepted))[:20]


def match_directory_links(directory_url, links, member_names, existing_profiles=None):
    """Match a unique full-name URL slug or exact anchor label, never partial names.

    URL and anchor text are research signals only, not proof of identity.
    """
    directory = urlparse(directory_url)
    profiles = existing_profiles if isinstance(existing_profiles, dict) else {}
    names = {}
    for number, name in member_names.items():
        if not isinstance(name, str) or profiles.get(number):
            continue
        slug = _slug(name)
        if slug:
            names.setdefault(slug, []).append(str(number))
    found = {}
    for item in links[:300]:
        url = item.get("url") if isinstance(item, dict) else item
        label = item.get("text", "") if isinstance(item, dict) else ""
        if not isinstance(url, str):
            continue
        try:
            p = urlparse(url)
            if (p.scheme != "https" or p.hostname != directory.hostname
                    or p.username or p.password or p.port not in (None, 443)
                    or p.query or p.fragment or not p.path.strip("/")):
                continue
            segment = _slug(unquote(p.path.rstrip("/").split("/")[-1]))
            label_slug = _slug(label) if isinstance(label, str) else ""
        except (ValueError, UnicodeError):
            continue
        slug_matches = names.get(segment, [])
        label_matches = names.get(label_slug, [])
        # If URL and label each identify different people, reject the link.
        if slug_matches and label_matches and slug_matches != label_matches:
            continue
        matches = slug_matches or label_matches
        if len(matches) == 1 and url != directory_url:
            found.setdefault(matches[0], url)
    return found

async def discover_cantonal_directory_candidates(member_names, verified_social_url, *,
                                                   existing_profiles=None, checked_at=None,
                                                   path=None):
    from .main import scan_homepage
    from .public_https import PublicHTTPSClient
    from .social_evidence import _utc_now
    directories = configured_cantonal_directories(path)
    diagnostic = {"configured": len(directories), "matched_pages": 0,
                  "parsed_directories": 0, "candidates": 0}
    if not directories:
        return {}, diagnostic
    semaphore = asyncio.Semaphore(4)
    reports = [{} for _ in directories]
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(scan_homepage(client, url, semaphore, reports[i])
                                         for i, url in enumerate(directories)))
    jobs = {}
    for source, links, report in zip(directories, results, reports):
        if report.get("status") != "parsed":
            continue
        diagnostic["parsed_directories"] += 1
        # Scanner returns social links separately; internal links are in the report.
        internal = report.get("directory_links", []) + report.get("internal_page_candidates", [])
        if isinstance(links, dict):
            internal += [value for value in links.values() if isinstance(value, str)]
        for number, url in match_directory_links(source, internal, member_names,
                                                   existing_profiles).items():
            jobs.setdefault(number, url)
    jobs = dict(list(jobs.items())[:40])
    diagnostic["matched_pages"] = len(jobs)
    if not jobs:
        return {}, diagnostic
    page_reports = [{} for _ in jobs]
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        pages = await asyncio.gather(*(scan_homepage(client, url, semaphore, page_reports[i])
                                       for i, url in enumerate(jobs.values())))
    found = {}
    for (number, source), links, report in zip(jobs.items(), pages, page_reports):
        if report.get("status") != "parsed":
            continue
        candidates = list(links.values()) + report.get("social_url_candidates", []) + report.get("raw_social_urls", []) + report.get("data_social_urls", [])
        for url in dict.fromkeys(u for u in candidates[:100] if isinstance(u, str)):
            try:
                platform = verified_social_url(url)
            except (ValueError, UnicodeError):
                continue
            if platform:
                found.setdefault(number, []).append({
                    "platform": platform, "url": url,
                    "source_type": "verified_cantonal_party_directory",
                    "source_url": source, "checked_at": checked_at or _utc_now(),
                    "verification_status": "requires_identity_verification",
                    "auto_accept": False,
                    "reason": "directory_name_match_requires_manual_identity_confirmation",
                })
    diagnostic["candidates"] = sum(map(len, found.values()))
    return found, diagnostic
