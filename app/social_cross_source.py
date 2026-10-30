"""Cross-source corroboration and collision warnings for manual social review."""
from collections import defaultdict
from .social_quality import canonical_url


def annotate_cross_source(queue, profiles=None):
    """Add evidence summaries without approving candidates or modifying links.

    Only distinct source URLs count as independent observations. Shared
    candidate URLs across different members are flagged as identity conflicts.
    """
    if not isinstance(queue, dict):
        return queue
    owners = defaultdict(set)
    for number, entries in queue.items():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if isinstance(item, dict):
                key = canonical_url(item.get("url"))
                if key:
                    owners[key].add(str(number))
    if isinstance(profiles, dict):
        for number, links in profiles.items():
            if isinstance(links, dict):
                for url in links.values():
                    key = canonical_url(url)
                    if key:
                        owners[key].add(str(number))
    for number, entries in queue.items():
        if not isinstance(entries, list):
            continue
        observations = defaultdict(set)
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = canonical_url(item.get("url"))
            source = canonical_url(item.get("source_url"))
            if key and source:
                observations[key].add(source)
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = canonical_url(item.get("url"))
            sources = sorted(observations.get(key, set()))
            conflicts = sorted(owners.get(key, set()) - {str(number)}) if key else []
            item["independent_source_count"] = len(sources)
            item["source_evidence_urls"] = sources
            item["identity_conflict_person_numbers"] = conflicts
            item["requires_identity_verification"] = True
            if conflicts:
                item["review_priority_reasons"] = list(item.get("review_priority_reasons", [])) + ["cross_member_identity_conflict"]
            elif len(sources) > 1:
                item["review_priority_reasons"] = list(item.get("review_priority_reasons", [])) + ["multiple_source_pages"]
    return queue
