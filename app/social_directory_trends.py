"""Track consecutive failures and prioritize manual source review."""

from datetime import datetime, timezone

FAILURE_THRESHOLD = 3
MAX_STREAK = 999


def annotate_directory_failures(current, previous):
    """Update failure streaks for identical URLs; no automatic source disabling.

    Only fetch/parse failures count. A parsed page with no matching member
    links is not necessarily broken (members may already be covered).
    """
    prior = {}
    if isinstance(previous, dict):
        for item in previous.get("sources", []):
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                prior[item["url"]] = item
    for item in current.get("sources", []):
        if not isinstance(item, dict):
            continue
        old = prior.get(item.get("url"), {})
        failed = item.get("status") != "parsed"
        previous_streak = old.get("failure_streak", 0)
        if type(previous_streak) is not int or previous_streak < 0:
            previous_streak = 0
        streak = min(MAX_STREAK, previous_streak + 1) if failed else 0
        item["failure_streak"] = streak
        item["persistent_failure"] = streak >= FAILURE_THRESHOLD
        checked_at = current.get("checked_at") or datetime.now(timezone.utc).isoformat()
        history = old.get("failure_history", [])
        if not isinstance(history, list):
            history = []
        history = [event for event in history if isinstance(event, dict)][-9:]
        if failed:
            history.append({"checked_at": checked_at,
                            "status": str(item.get("status") or "unknown")[:80],
                            "reason": str(item.get("reason") or "")[:200]})
        item["failure_history"] = history[-10:]
        last_success = old.get("last_success_at")
        item["last_success_at"] = checked_at if not failed else (
            last_success if isinstance(last_success, str) else None)
        if item["persistent_failure"]:
            priority, recommendation = "high", "Quellen-URL, Erreichbarkeit und HTML-Struktur prüfen"
        elif failed:
            priority, recommendation = "medium", "Beim nächsten Refresh erneut prüfen"
        elif item.get("matched_members", 0) == 0:
            priority, recommendation = "low", "Mitgliederabdeckung und Linkstruktur prüfen"
        else:
            priority, recommendation = "none", "Keine Massnahme erforderlich"
        item["review_priority"] = priority
        item["recommendation"] = recommendation
    current["persistent_failure_count"] = sum(
        item.get("persistent_failure") is True for item in current.get("sources", [])
        if isinstance(item, dict))
    ranks = {"high": 0, "medium": 1, "low": 2}
    current["review_queue"] = sorted(
        ({"url": item.get("url"), "priority": item["review_priority"],
          "failure_streak": item["failure_streak"],
          "last_success_at": item["last_success_at"],
          "failure_history": item["failure_history"],
          "recommendation": item["recommendation"]}
         for item in current.get("sources", [])
         if isinstance(item, dict) and item.get("review_priority") in ranks),
        key=lambda entry: (ranks[entry["priority"]],
                           -entry["failure_streak"], str(entry["url"])))
    return current
