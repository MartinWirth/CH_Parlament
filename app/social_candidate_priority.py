"""Explainable priority hints for manual social-profile review.

These scores rank evidence sources; they do not establish personal identity.
"""
from .social_quality import canonical_url, _profile_review_reason

SOURCE_PRIORITY = {
    "parliament_listed_personal_homepage": 70,
    "official_party_profile": 60,
    "external_candidate": 30,
}


def annotate_candidates(queue):
    """Add review hints without changing candidate URLs or approval status."""
    if not isinstance(queue, dict):
        return queue
    for entries in queue.values():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            source = item.get("source_type")
            url = item.get("url")
            platform = item.get("platform")
            score = SOURCE_PRIORITY.get(source, 20)
            reasons = ["source:" + str(source or "unknown")]
            if not isinstance(url, str) or canonical_url(url) is None:
                score = 0
                reasons.append("invalid_profile_url")
            elif isinstance(platform, str):
                warning = _profile_review_reason(url, platform)
                if warning:
                    score = max(0, score - 40)
                    reasons.append(warning)
            source_url = item.get("source_url")
            if not isinstance(source_url, str) or canonical_url(source_url) is None:
                score = max(0, score - 20)
                reasons.append("missing_or_invalid_source_url")
            item["review_priority_score"] = score
            item["review_priority_reasons"] = reasons
    return queue
