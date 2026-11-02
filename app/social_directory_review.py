"""Persistent manual status for configured directory URLs; separate from scan results."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

STATUSES = {"in_review", "resolved", "deferred"}
MAX_COMMENT = 1000


def review_path():
    return Path(os.getenv("SOCIAL_DIRECTORY_REVIEW_PATH", ".social_directory_review.json"))


def read_reviews():
    try:
        data = json.loads(review_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_review(language, url, status, comment, configured_urls):
    if language not in ("DE", "FR", "IT", "RM") or status not in STATUSES:
        raise ValueError("Invalid language or status")
    if not isinstance(url, str) or url not in configured_urls:
        raise ValueError("Unknown configured source")
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT:
        raise ValueError("Invalid comment")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Invalid source URL")
    record = {"status": status, "comment": comment.strip(),
              "reviewed_at": datetime.now(timezone.utc).isoformat()}
    path = review_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = read_reviews()
    data.setdefault(language, {})[url] = record
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return record


def reviews_for_sources(language, sources):
    records = read_reviews().get(language, {})
    if not isinstance(records, dict):
        return {}
    urls = {item.get("url") for item in sources if isinstance(item, dict)}
    return {url: record for url, record in records.items()
            if url in urls and isinstance(record, dict)}
