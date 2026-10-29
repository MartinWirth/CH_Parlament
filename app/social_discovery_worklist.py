"""Research shortcuts for missing personal social accounts, never auto-approved."""
from urllib.parse import urlencode
from .social_check_history import LANGUAGES

SITES = {"LinkedIn": "linkedin.com/in/", "Instagram": "instagram.com/",
         "Facebook": "facebook.com/", "X": "x.com/", "YouTube": "youtube.com/@"}


def discovery_worklist(language, *, limit=50, platform="all"):
    if language not in LANGUAGES or type(limit) is not int or not 1 <= limit <= 250:
        raise ValueError("Invalid discovery query")
    if platform != "all" and platform not in SITES:
        raise ValueError("Invalid platform")
    from .main import read_profile_snapshot, verified_social_url
    from .social_review import apply_decisions
    snapshot = read_profile_snapshot()
    apply_decisions(snapshot, language)
    data = snapshot.get(language, {})
    names = data.get("member_names", {})
    profiles = data.get("profiles", {})
    candidates = data.get("review_candidates", {})
    if not isinstance(names, dict):
        names = {}
    if not isinstance(profiles, dict):
        profiles = {}
    if not isinstance(candidates, dict):
        candidates = {}
    items = []
    for number, name in names.items():
        if not isinstance(number, str) or not isinstance(name, str) or not name.strip():
            continue
        links = profiles.get(number, {})
        if not isinstance(links, dict):
            links = {}
        pending = candidates.get(number, [])
        if not isinstance(pending, list):
            pending = []
        queued = {entry.get("platform") for entry in pending if isinstance(entry, dict)
                  and entry.get("review_status") not in ("approved", "rejected")}
        existing = {verified_social_url(url) for url in links.values() if isinstance(url, str)}
        for network, site in SITES.items():
            if platform != "all" and platform != network:
                continue
            if network in existing or network in queued:
                continue
            query = f'"{name}" Nationalrat Ständerat Schweiz site:{site}'
            items.append({"person_number": number, "name": name, "platform": network,
                          "search_url": "https://www.google.com/search?" + urlencode({"q": query}),
                          "existing_count": len(existing), "requires_manual_verification": True})
    items.sort(key=lambda x: (x["existing_count"], x["name"].casefold(), x["platform"]))
    return {"language": language, "total": len(items), "items": items[:limit],
            "note": "Search links are research shortcuts, not discovered or verified accounts. "
                    "Confirm identity and source evidence before manual approval."}
