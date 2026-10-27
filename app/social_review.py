"""Persistent, explicit administrator decisions for social profile candidates."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from .social_evidence import evidence_for_sources


def decision_path():
    return Path(os.getenv("SOCIAL_REVIEW_DECISIONS_PATH", ".social_review_decisions.json"))


def read_decisions():
    try:
        value = json.loads(decision_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_decisions(data):
    path = decision_path()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def apply_decisions(snapshot, language, decisions=None):
    """Apply only explicitly approved URLs still present in the review queue."""
    decisions = decisions if decisions is not None else read_decisions()
    data = snapshot.get(language, {})
    queue = data.get("review_candidates", {})
    if not isinstance(queue, dict):
        return
    language_decisions = decisions.get(language, {})
    for number, entries in queue.items():
        if not isinstance(entries, list):
            continue
        for candidate in entries:
            if not isinstance(candidate, dict):
                continue
            url = candidate.get("url")
            platform = candidate.get("platform")
            record = language_decisions.get(number, {}).get(url, {})
            state = record.get("decision")
            if state not in ("approved", "rejected"):
                continue
            candidate["review_status"] = state
            candidate["reviewed_at"] = record.get("reviewed_at")
            if state == "rejected" and isinstance(platform, str):
                sources = data.get("profiles_by_source", {}).get(number, {})
                approved = sources.get("Admin-Freigabe", {})
                if approved.get(platform) == url:
                    approved.pop(platform, None)
                    evidence = data.get("profile_evidence", {}).get(number, [])
                    evidence[:] = [item for item in evidence if not (
                        item.get("url") == url and item.get("source_type") == "Admin-Freigabe")]
                    other = any(isinstance(links, dict) and links.get(platform) == url
                                for key, links in sources.items() if key != "Admin-Freigabe")
                    profiles = data.get("profiles", {}).get(number, {})
                    if not other and profiles.get(platform) == url:
                        profiles.pop(platform, None)
                        if not profiles:
                            data["profiles"].pop(number, None)
                continue
            if state != "approved" or not isinstance(platform, str):
                continue
            # Preserve previously verified profiles; never overwrite them.
            data.setdefault("profiles", {}).setdefault(number, {}).setdefault(platform, url)
            sources = data.setdefault("profiles_by_source", {}).setdefault(number, {})
            sources.setdefault("Admin-Freigabe", {}).setdefault(platform, url)
            evidence = data.setdefault("profile_evidence", {}).setdefault(number, [])
            if not any(item.get("url") == url and item.get("source_type") == "Admin-Freigabe"
                       for item in evidence):
                evidence.append({
                    "platform": platform, "url": url,
                    "source_type": "Admin-Freigabe",
                    "source_url": candidate.get("source_url"),
                    "checked_at": record.get("reviewed_at"),
                    "verification_status": "manually_approved",
                })


def decide(snapshot, language, number, url, decision):
    if language not in ("DE", "FR", "IT", "RM") or decision not in ("approved", "rejected"):
        raise ValueError("Invalid review action")
    data = snapshot.get(language, {})
    entries = data.get("review_candidates", {}).get(number, [])
    if not any(isinstance(entry, dict) and entry.get("url") == url for entry in entries):
        raise LookupError("Candidate not found in current snapshot")
    decisions = read_decisions()
    record = {"decision": decision, "reviewed_at": datetime.now(timezone.utc).isoformat()}
    decisions.setdefault(language, {}).setdefault(number, {})[url] = record
    write_decisions(decisions)
    apply_decisions(snapshot, language, decisions)
    return record
