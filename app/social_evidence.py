"""Source-aware personal social profile evidence and manual review queue.

Candidate records never auto-attribute an identity. Accepted links originate
from the existing parliament, party and personal-homepage verification paths.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlparse


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _source_url(source, number, homepages):
    if source == "Parlamentarier-Homepage":
        return homepages.get(number)
    return None  # party / parliament URLs require per-link source evidence


def evidence_for_sources(by_source, homepages, checked_at=None):
    checked_at = checked_at or _utc_now()
    result = {}
    for number, sources in by_source.items():
        records = []
        for source, links in sources.items():
            if not isinstance(links, dict):
                continue
            for platform, url in links.items():
                if not isinstance(url, str):
                    continue
                records.append({
                    "platform": platform, "url": url,
                    "source_type": source,
                    "source_url": _source_url(source, number, homepages),
                    "checked_at": checked_at,
                    "verification_status": "source_attributed",
                })
        if records:
            result[number] = records
    return result


def load_review_candidates(member_names, verified_social_url, path=None, checked_at=None):
    """Load optional, externally researched candidate URLs, never auto-accept.

    Match a stable person number AND full current member name to avoid
    attributing similarly named people. Do not perform network requests.
    """
    checked_at = checked_at or _utc_now()
    path = Path(path or os.getenv("SOCIAL_REVIEW_CANDIDATES_PATH", "social_review_candidates.json"))
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {"configured": 0, "accepted_for_review": 0, "rejected": 0}
    if not isinstance(entries, list):
        return {}, {"configured": 0, "accepted_for_review": 0, "rejected": 1}
    result, rejected, seen = {}, 0, set()
    for item in entries[:2000]:
        if not isinstance(item, dict):
            rejected += 1
            continue
        number = str(item.get("person_number", ""))
        name = item.get("name")
        url = item.get("url")
        source = item.get("source_url")
        if (not isinstance(name, str) or
                name.casefold().strip() != member_names.get(number, "").casefold().strip() or
                not isinstance(url, str) or not isinstance(source, str) or
                not source.startswith("https://") or not url.startswith("https://")):
            rejected += 1
            continue
        try:
            platform = verified_social_url(url)
            parsed_source = urlparse(source)
            valid_source = (parsed_source.scheme == "https" and bool(parsed_source.hostname)
                            and not parsed_source.username and not parsed_source.password
                            and parsed_source.port in (None, 443))
        except (ValueError, UnicodeError):
            rejected += 1
            continue
        if not platform or not valid_source or (number, url) in seen:
            rejected += 1
            continue
        seen.add((number, url))
        result.setdefault(number, []).append({
            "platform": platform, "url": url, "source_type": "external_candidate",
            "source_url": source, "checked_at": checked_at,
            "verification_status": "requires_identity_verification",
            "auto_accept": False,
        })
    return result, {"configured": len(entries), "accepted_for_review": len(seen),
                    "rejected": rejected + max(0, len(entries) - 2000)}


def discover_party_review_candidates(party_diagnostic, member_names, verified_social_url,
                                     checked_at=None):
    """Collect rejected social links already seen on official member party pages.

    The upstream roster discovery binds each page to a current member by exact
    name. Links still require manual identity verification; party footer links
    must never become personal profiles automatically.
    """
    checked_at = checked_at or _utc_now()
    result, seen = {}, set()
    for page in party_diagnostic.get("pages", [])[:500]:
        if not isinstance(page, dict):
            continue
        number = str(page.get("person_number", ""))
        if not number or page.get("name") != member_names.get(number):
            continue
        source = page.get("profile_url")
        if not isinstance(source, str) or not source.startswith("https://"):
            continue
        try:
            parsed = urlparse(source)
            if (not parsed.hostname or parsed.username or parsed.password or
                    parsed.port not in (None, 443)):
                continue
        except ValueError:
            continue
        rejected = page.get("rejected", {})
        if not isinstance(rejected, dict):
            continue
        for entry in rejected.values():
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str):
                continue
            try:
                platform = verified_social_url(url)
            except (ValueError, UnicodeError):
                continue
            if not platform or (number, url) in seen:
                continue
            seen.add((number, url))
            result.setdefault(number, []).append({
                "platform": platform, "url": url, "source_type": "official_party_profile",
                "source_url": source, "checked_at": checked_at,
                "verification_status": "requires_identity_verification",
                "auto_accept": False,
                "reason": entry.get("reason", "identity_not_confirmed"),
            })
    return result
