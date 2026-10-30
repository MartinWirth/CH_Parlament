"""Offline social-profile quality audit: no network access and no automatic deletions."""
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _profile_review_reason(url, platform):
    """Flag non-personal destinations for manual review, without deleting links."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    parts = [part.lower() for part in parsed.path.split("/") if part]
    if not parts:
        return "missing_profile_path"
    if platform == "LinkedIn" and parts[0] in {"company", "school", "jobs", "feed", "posts", "groups", "events"}:
        return "non_personal_linkedin_page"
    if platform == "Instagram" and parts[0] in {"p", "reel", "reels", "stories", "explore", "accounts", "direct"}:
        return "instagram_content_not_profile"
    if platform == "Facebook" and parts[0] in {"groups", "events", "watch", "share", "sharer", "photo", "photos"}:
        return "facebook_content_not_profile"
    if platform == "YouTube" and parts[0] in {"watch", "shorts", "playlist", "results", "feed"}:
        return "youtube_content_not_channel"
    if platform == "X" and parts[0] in {"home", "search", "explore", "intent", "i", "hashtag"}:
        return "x_content_not_profile"
    return None


def canonical_url(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme.lower() != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
        path = parsed.path.rstrip("/") or "/"
        host = parsed.hostname.lower().rstrip(".")
        # Keep existing canonical URLs stable for persisted review decisions.
        if host == "m.facebook.com":
            host = "www.facebook.com"
        query = ""
        # Facebook's legacy profile.php route identifies the account by its id
        # query parameter. Dropping it would merge unrelated people's profiles.
        if host in ("facebook.com", "www.facebook.com") and path.lower() == "/profile.php":
            pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
            identifiers = [value for key, value in pairs if key == "id"]
            if len(identifiers) != 1 or not identifiers[0].strip():
                return None
            query = urlencode({"id": identifiers[0]})
        return urlunsplit(("https", host, path, query, ""))
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
            reason = _profile_review_reason(url, platform)
            if reason:
                issues.append({"type": "review", "person_number": str(number),
                               "platform": str(platform), "url": url, "reason": reason})
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
            "duplicates": sum(i["type"] == "duplicate" for i in issues),
            "needs_review": sum(i["type"] == "review" for i in issues)}
