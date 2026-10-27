"""Offline social-profile quality audit: no network access and no automatic deletions."""
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit


def canonical_url(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme.lower() != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit(("https", parsed.hostname.lower(), path, "", ""))
    except (ValueError, UnicodeError):
        return None


def audit_profiles(profiles, verified_social_url):
    """Flag invalid links and cross-member duplicates without mutating profiles."""
    issues, owners = [], defaultdict(list)
    checked = 0
    if not isinstance(profiles, dict):
        return {"checked": 0, "issues": [], "issue_count": 0, "invalid": 0, "duplicates": 0}
    for number, links in profiles.items():
        if not isinstance(links, dict):
            continue
        for platform, url in links.items():
            checked += 1
            canonical = canonical_url(url)
            try:
                detected = verified_social_url(url) if canonical else None
            except (ValueError, UnicodeError, TypeError):
                detected = None
            if not detected or detected != platform:
                issues.append({"type": "invalid", "person_number": str(number),
                               "platform": str(platform), "url": url if isinstance(url, str) else "",
                               "reason": "invalid_url_or_platform"})
                continue
            owners[canonical].append((str(number), str(platform), url))
    for canonical, records in owners.items():
        members = sorted({number for number, _, _ in records})
        if len(members) < 2:
            continue
        for number, platform, url in records:
            issues.append({"type": "duplicate", "person_number": number,
                           "platform": platform, "url": url,
                           "other_person_numbers": [n for n in members if n != number],
                           "reason": "same_profile_assigned_to_multiple_members"})
    return {"checked": checked, "issues": issues, "issue_count": len(issues),
            "invalid": sum(i["type"] == "invalid" for i in issues),
            "duplicates": sum(i["type"] == "duplicate" for i in issues)}
