"""Populate review-only candidates from party pages recorded by refresh.

Run after an authenticated refresh:
    python -m app.party_candidate_cli
No network calls are made. Candidates are never copied to accepted profiles.
"""
import json
import os
from pathlib import Path

from .social_evidence import discover_party_review_candidates
from .main import ROOT, verified_social_url


def update_snapshot(path=None):
    target = Path(path or os.getenv("SOCIAL_PROFILE_SNAPSHOT_PATH",
                                   str(ROOT.parent / ".social_profiles_snapshot.json")))
    snapshot = json.loads(target.read_text(encoding="utf-8"))
    counts = {}
    for language, data in snapshot.items():
        if not isinstance(data, dict):
            continue
        diagnostic = data.get("debug", {}).get("party_sources", {})
        pages = diagnostic.get("pages", [])
        member_names = {
            str(item["person_number"]): item["name"]
            for item in pages
            if isinstance(item, dict) and item.get("person_number") and isinstance(item.get("name"), str)
        }
        discovered = discover_party_review_candidates(
            diagnostic, member_names, verified_social_url)
        queue = data.setdefault("review_candidates", {})
        count = 0
        for number, entries in discovered.items():
            existing = queue.setdefault(number, [])
            seen = {item.get("url") for item in existing if isinstance(item, dict)}
            for entry in entries:
                if entry["url"] not in seen:
                    existing.append(entry)
                    seen.add(entry["url"])
                    count += 1
        counts[language] = count
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return counts


if __name__ == "__main__":
    print(json.dumps(update_snapshot()))
