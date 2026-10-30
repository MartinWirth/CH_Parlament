"""Deterministic research worklist for members without confirmed personal profiles.

Uses only the last saved snapshot; search hints are not identity evidence.
"""


def targeted_research_queue(snapshot, *, limit=100):
    names = snapshot.get("member_names", {})
    profiles = snapshot.get("profiles", {})
    candidates = snapshot.get("review_candidates", {})
    homepages = snapshot.get("homepage_urls", {})
    if not isinstance(names, dict):
        names = {}
    if not isinstance(profiles, dict):
        profiles = {}
    if not isinstance(candidates, dict):
        candidates = {}
    if not isinstance(homepages, dict):
        homepages = {}
    rows = []
    for number, name in names.items():
        number = str(number)
        if not isinstance(name, str) or not name.strip() or profiles.get(number):
            continue
        pending = candidates.get(number, [])
        if not isinstance(pending, list):
            pending = []
        pending = [item for item in pending if isinstance(item, dict)]
        homepage = homepages.get(number)
        if not isinstance(homepage, str) or not homepage.startswith("https://"):
            homepage = None
        rows.append({
            "person_number": number,
            "name": name.strip(),
            "candidate_count": len(pending),
            "has_homepage": bool(homepage),
            "homepage_url": homepage,
            "research_query": '"'+name.strip()+'" (LinkedIn OR Instagram OR Facebook OR X)',
            "priority": "review_existing" if pending else
                        "inspect_homepage" if homepage else "find_official_source",
            "requires_identity_verification": True,
            "auto_accept": False,
        })
    order = {"review_existing": 0, "inspect_homepage": 1, "find_official_source": 2}
    rows.sort(key=lambda row: (order[row["priority"]], -row["candidate_count"],
                                row["name"].casefold(), row["person_number"]))
    limit = max(1, min(int(limit), 500))
    return {"updated_at": snapshot.get("updated_at"),
            "total_uncovered": len(rows),
            "truncated": len(rows) > limit,
            "worklist": rows[:limit]}
