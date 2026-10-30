"""Track consecutive directory scan failures in the saved refresh snapshot."""

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
    current["persistent_failure_count"] = sum(
        item.get("persistent_failure") is True for item in current.get("sources", [])
        if isinstance(item, dict))
    return current
