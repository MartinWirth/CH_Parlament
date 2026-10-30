"""Deterministic, read-only coverage report for saved social discovery results."""
from collections import Counter
from .social_quality import canonical_url


def discovery_report(snapshot, *, limit=100):
    """Summarize candidates without implying their identity is verified."""
    profiles = snapshot.get("profiles", {})
    queue = snapshot.get("review_candidates", {})
    if not isinstance(profiles, dict):
        profiles = {}
    if not isinstance(queue, dict):
        queue = {}
    rows = []
    sources = Counter()
    seen = set()
    for number, entries in queue.items():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            key = canonical_url(url)
            if not key or (str(number), key) in seen:
                continue
            seen.add((str(number), key))
            source_type = item.get("source_type") or "unknown"
            sources[str(source_type)] += 1
            conflicts = item.get("identity_conflict_person_numbers", [])
            if not isinstance(conflicts, list):
                conflicts = []
            rows.append({
                "person_number": str(number),
                "platform": item.get("platform"),
                "url": url,
                "source_url": item.get("source_url"),
                "source_type": source_type,
                "priority": item.get("review_priority_score", 0),
                "independent_source_count": item.get("independent_source_count", 0),
                "identity_conflicts": conflicts,
                "requires_identity_verification": True,
                "auto_accept": False,
                "has_confirmed_profiles": bool(profiles.get(str(number))),
            })
    rows.sort(key=lambda item: (
        bool(item["identity_conflicts"]),
        item["has_confirmed_profiles"],
        -item["priority"] if isinstance(item["priority"], (int, float)) else 0,
        -item["independent_source_count"] if isinstance(item["independent_source_count"], int) else 0,
        item["person_number"], item["url"]))
    return {
        "updated_at": snapshot.get("updated_at"),
        "confirmed_member_count": sum(bool(value) for value in profiles.values()),
        "members_with_review_candidates": len({row["person_number"] for row in rows}),
        "members_without_confirmed_profiles_with_candidates": len({
            row["person_number"] for row in rows if not row["has_confirmed_profiles"]}),
        "total_distinct_candidates": len(rows),
        "candidates_by_source": dict(sorted(sources.items())),
        "identity_conflict_candidates": sum(bool(row["identity_conflicts"]) for row in rows),
        "candidates": rows[:max(0, min(limit, 500))],
        "truncated": len(rows) > max(0, min(limit, 500)),
    }
