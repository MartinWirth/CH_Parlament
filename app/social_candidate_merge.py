"""Merge candidate discoveries while preserving distinct source evidence.

No candidate is approved or attributed by this module.
"""
from .social_quality import canonical_url


def merge_review_candidates(queue, discovered):
    for number, entries in discovered.items():
        if not isinstance(entries, list):
            continue
        existing = queue.setdefault(number, [])
        if not isinstance(existing, list):
            continue
        by_url = {canonical_url(item.get("url")): item for item in existing
                  if isinstance(item, dict) and canonical_url(item.get("url"))}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = canonical_url(entry.get("url"))
            if not key:
                continue
            current = by_url.get(key)
            if current is None:
                current = dict(entry)
                current["source_observations"] = []
                existing.append(current)
                by_url[key] = current
            observations = current.setdefault("source_observations", [])
            if not isinstance(observations, list):
                observations = []
                current["source_observations"] = observations
            for candidate in (current, entry):
                source_url = candidate.get("source_url")
                source_type = candidate.get("source_type")
                if not isinstance(source_url, str) or not source_url.startswith("https://"):
                    continue
                observation = {"source_url": source_url, "source_type": source_type}
                if observation not in observations:
                    observations.append(observation)
            current["auto_accept"] = False
    return queue
