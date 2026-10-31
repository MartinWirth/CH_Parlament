"""Opt-in, session-authenticated administration of social-profile refresh jobs.

Set ADMIN_REFRESH_PASSWORD and SOCIAL_REFRESH_TOKEN on the server.
Run behind HTTPS; cookies are Secure and HttpOnly. Sessions and jobs are
process-local: deploy with a single worker until shared storage is added.
"""
import asyncio
from datetime import datetime, timezone
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter(prefix="/admin")
_sessions = {}
_login_failures = {}
_job = {"state": "idle", "started_at": None, "finished_at": None, "error": None}
_task = None
SESSION_SECONDS = 3600
LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_FAILURES = 5
MAX_TRACKED_CLIENTS = 2048
MAX_ACTIVE_SESSIONS = 2048


def _prune_auth_state(now=None):
    """Remove expired state and enforce process-local memory bounds."""
    if now is None:
        now = time.monotonic()
    for token, expires in list(_sessions.items()):
        if expires <= now:
            _sessions.pop(token, None)
    for client, timestamps in list(_login_failures.items()):
        recent = [t for t in timestamps if now - t < LOGIN_WINDOW_SECONDS]
        if recent:
            _login_failures[client] = recent[-MAX_LOGIN_FAILURES:]
        else:
            _login_failures.pop(client, None)
    while len(_login_failures) > MAX_TRACKED_CLIENTS:
        oldest = min(_login_failures, key=lambda key: _login_failures[key][-1])
        _login_failures.pop(oldest, None)
    while len(_sessions) > MAX_ACTIVE_SESSIONS:
        oldest = min(_sessions, key=_sessions.get)
        _sessions.pop(oldest, None)



def _now():
    return datetime.now(timezone.utc).isoformat()


def _same_origin(request):
    origin = request.headers.get("origin")
    # Require Origin and a custom header for state-changing browser requests.
    return bool(origin and origin.rstrip("/") == str(request.base_url).rstrip("/")
                and request.headers.get("X-Admin-Action") == "1")


def _authorized(request):
    _prune_auth_state()
    cookie = request.cookies.get("chp_admin")
    if not cookie:
        return False
    expires = _sessions.get(cookie, 0)
    if expires <= time.monotonic():
        _sessions.pop(cookie, None)
        return False
    return True


def _require(request):
    if not _authorized(request):
        raise HTTPException(401, "Administrator login required")


@router.get("")
async def admin_page():
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin.html")


@router.post("/login")
async def admin_login(request: Request):
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    configured = os.getenv("ADMIN_REFRESH_PASSWORD")
    if not configured or not os.getenv("SOCIAL_REFRESH_TOKEN"):
        raise HTTPException(503, "Admin refresh is not configured")
    client_id = request.client.host if request.client else "unknown"
    _prune_auth_state()
    recent = _login_failures.get(client_id, [])
    if len(recent) >= MAX_LOGIN_FAILURES:
        raise HTTPException(429, "Too many login attempts; retry later")
    data = await request.json()
    password = data.get("password", "") if isinstance(data, dict) else ""
    if not isinstance(password, str) or not secrets.compare_digest(password, configured):
        if client_id not in _login_failures:
            _login_failures[client_id] = recent
        recent.append(time.monotonic())
        _prune_auth_state()
        raise HTTPException(403, "Invalid credentials")
    _login_failures.pop(client_id, None)
    from fastapi.responses import JSONResponse
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.monotonic() + SESSION_SECONDS
    _prune_auth_state()
    response = JSONResponse({"authenticated": True})
    response.set_cookie("chp_admin", token, max_age=SESSION_SECONDS,
                        httponly=True, secure=True, samesite="strict", path="/admin")
    return response


@router.post("/logout")
async def admin_logout(request: Request):
    _require(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    _sessions.pop(request.cookies.get("chp_admin"), None)
    from fastapi.responses import JSONResponse
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("chp_admin", path="/admin", secure=True, httponly=True, samesite="strict")
    return response


@router.get("/status")
async def admin_status(request: Request):
    _require(request)
    return dict(_job)


async def _execute(language, force):
    global _job
    try:
        # Imported lazily to avoid circular import during app initialization.
        from .main import _run_social_profiles_refresh, _refresh_lock
        async with _refresh_lock:
            result = await _run_social_profiles_refresh(language, force)
        _job.update(state="completed", finished_at=_now(),
                    updated_at=result.get("updated_at") if isinstance(result, dict) else None)
    except Exception:
        # Do not expose potentially sensitive exception details to the browser.
        _job.update(state="failed", finished_at=_now(), error="Refresh failed; check server logs")
        import logging
        logging.getLogger("ch_parlament").exception("Admin refresh failed")


@router.post("/refresh")
async def admin_refresh(request: Request):
    global _task
    _require(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    if not os.getenv("SOCIAL_REFRESH_TOKEN"):
        raise HTTPException(503, "Refresh is not configured")
    data = await request.json()
    language = data.get("language", "DE") if isinstance(data, dict) else None
    force = data.get("force", False) if isinstance(data, dict) else None
    if language not in ("DE", "FR", "IT", "RM") or type(force) is not bool:
        raise HTTPException(422, "Invalid refresh parameters")
    from .main import _refresh_lock
    if (_task and not _task.done()) or _refresh_lock.locked():
        raise HTTPException(409, "Refresh already running")
    _job.clear()
    _job.update(state="running", started_at=_now(), finished_at=None,
                error=None, language=language)
    _task = asyncio.create_task(_execute(language, force))
    return dict(_job)


@router.get("/review")
async def admin_review(request: Request, language: str = "DE"):
    _require(request)
    if language not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    from .main import read_profile_snapshot
    from .social_review import apply_decisions
    snapshot = read_profile_snapshot()
    apply_decisions(snapshot, language)
    data = snapshot.get(language, {})
    return {"language": language, "updated_at": data.get("updated_at"),
            "candidates": data.get("review_candidates", {})}


@router.get("/discovery-report")
async def admin_discovery_report(request: Request, language: str = "DE", limit: int = 100):
    """Authenticated summary of the last saved discovery snapshot."""
    _require(request)
    if language not in ("DE", "FR", "IT", "RM") or not 1 <= limit <= 500:
        raise HTTPException(422, "Invalid report parameters")
    from .main import read_profile_snapshot
    from .social_review import apply_decisions
    from .social_discovery_report import discovery_report
    snapshot = read_profile_snapshot()
    apply_decisions(snapshot, language)
    return {"language": language, **discovery_report(snapshot.get(language, {}), limit=limit)}



@router.get("/directory-source-health")
async def admin_directory_source_health(request: Request, language: str = "DE"):
    """Read-only per-source diagnostics from the most recent saved refresh."""
    _require(request)
    if language not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    from .main import read_profile_snapshot
    snapshot = read_profile_snapshot().get(language, {})
    report = snapshot.get("debug", {}).get("review_candidates", {}).get(
        "cantonal_directory_discovery", {})
    return {"language": language, "updated_at": snapshot.get("updated_at"),
            "configured": report.get("configured", 0),
            "parsed_directories": report.get("parsed_directories", 0),
            "persistent_failure_count": report.get("persistent_failure_count", 0),
            "sources": report.get("sources", []),
            "review_queue": report.get("review_queue", [])}

@router.get("/targeted-research")
async def admin_targeted_research(request: Request, language: str = "DE", limit: int = 100):
    """Read-only worklist for uncovered members; never auto-assign identities."""
    _require(request)
    if language not in ("DE", "FR", "IT", "RM") or not 1 <= limit <= 500:
        raise HTTPException(422, "Invalid research parameters")
    from .main import read_profile_snapshot
    from .social_review import apply_decisions
    from .social_targeted_research import targeted_research_queue
    snapshot = read_profile_snapshot()
    apply_decisions(snapshot, language)
    return {"language": language, **targeted_research_queue(snapshot.get(language, {}), limit=limit)}


@router.post("/review")
async def admin_review_decision(request: Request):
    _require(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    from .main import read_profile_snapshot, save_profile_snapshot, _refresh_lock
    from .social_review import decide
    data = await request.json()
    if not isinstance(data, dict) or set(data) != {"language", "person_number", "url", "decision"}:
        raise HTTPException(422, "Invalid review request")
    language, number, url, decision = (data[k] for k in
        ("language", "person_number", "url", "decision"))
    if not all(isinstance(v, str) for v in (language, number, url, decision)):
        raise HTTPException(422, "Invalid review values")
    if _refresh_lock.locked():
        raise HTTPException(409, "Refresh already running")
    async with _refresh_lock:
        snapshot = read_profile_snapshot()
        try:
            record = decide(snapshot, language, number, url, decision)
        except ValueError:
            raise HTTPException(422, "Invalid review action")
        except LookupError:
            raise HTTPException(404, "Candidate not found")
        save_profile_snapshot(snapshot)
    return {"ok": True, **record}


@router.get("/targeted-ui.js")
async def admin_targeted_script():
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin-targeted.js",
                        media_type="text/javascript")


@router.get("/discovery-ui.js")
async def admin_discovery_script():
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin-discovery.js",
                        media_type="text/javascript")


@router.get("/directory-health-ui.js")
async def admin_directory_health_script():
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin-directory-health.js",
                        media_type="text/javascript")


@router.get("/review-ui.js")
async def admin_review_script():
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin-review.js", media_type="text/javascript")


@router.get("/quality")
async def admin_quality(request: Request, language: str = "DE"):
    """Read-only audit of published profiles; never remove existing links."""
    _require(request)
    if language not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    from .main import read_profile_snapshot, verified_social_url
    from .social_review import apply_decisions
    from .social_quality import audit_profiles
    snapshot = read_profile_snapshot()
    apply_decisions(snapshot, language)
    data = snapshot.get(language, {})
    return {"language": language, "updated_at": data.get("updated_at"),
            **audit_profiles(data.get("profiles", {}), verified_social_url)}


_live_job = {"state": "idle", "results": [], "checked": 0}
_live_task = None


async def _execute_live_check(language):
    try:
        from .main import read_profile_snapshot, verified_social_url
        from .social_review import apply_decisions
        from .social_live_check import check_links
        snapshot = read_profile_snapshot()
        apply_decisions(snapshot, language)
        profiles = snapshot.get(language, {}).get("profiles", {})
        report = await check_links(profiles, verified_social_url)
        from .social_quality import audit_profiles
        quality = audit_profiles(profiles, verified_social_url)
        report["quality_snapshot"] = {"checked": quality["checked"],
            "invalid": quality["invalid"], "duplicates": quality["duplicates"]}
        from .social_check_history import save_report
        save_report(language, report)
        _live_job.update(state="completed", **report)
    except Exception:
        import logging
        logging.getLogger("ch_parlament").exception("Social link check failed")
        _live_job.update(state="failed", error="Link check failed; check server logs")


@router.post("/live-check")
async def admin_start_live_check(request: Request):
    global _live_task
    _require(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    data = await request.json()
    if not isinstance(data, dict) or set(data) != {"language"} or data["language"] not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    if (_live_task is not None and not _live_task.done()) or _live_job.get("state") == "running":
        raise HTTPException(409, "Link check already running")
    _live_job.clear()
    _live_job.update(state="running", language=data["language"], checked=0, results=[])
    _live_task = asyncio.create_task(_execute_live_check(data["language"]))
    return {"state": "running"}


@router.get("/live-check")
async def admin_live_check_status(request: Request):
    _require(request)
    return dict(_live_job)


@router.get("/live-check/history")
async def admin_live_check_history(request: Request, language: str = "DE"):
    _require(request)
    if language not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    from .social_check_history import get_history
    return {"language": language, "runs": get_history(language, limit=10)}


@router.get("/live-check/schedule")
async def admin_live_check_schedule(request: Request):
    _require(request)
    from .social_check_scheduler import interval_hours
    from .social_check_history import latest_check_time
    hours = interval_hours()
    languages = [v.strip().upper() for v in os.getenv("SOCIAL_CHECK_LANGUAGES", "DE").split(",")]
    return {"enabled": bool(hours), "interval_hours": hours,
            "languages": languages if hours else [],
            "last_checks": {lang: latest_check_time(lang) for lang in languages
                            if hours and lang in ("DE", "FR", "IT", "RM")}}


@router.get("/live-check/alerts")
async def admin_live_check_alerts(request: Request, language: str = "DE", search: str = "",
                                  platform: str = "", severity: str = "all", state: str = "all",
                                  date_from: str = "", date_to: str = "", new_only: bool = False):
    _require(request)
    if language not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    from .social_alerts import latest_alerts
    from .social_alert_filters import filter_alerts
    report = latest_alerts(language)
    try:
        filtered = filter_alerts(report["alerts"], search=search, platform=platform,
                                 severity=severity, state=state, date_from=date_from,
                                 date_to=date_to, new_only=new_only, checked_at=report["checked_at"])
    except ValueError:
        raise HTTPException(422, "Invalid alert filters")
    return {**report, "alerts": filtered, "filtered_count": len(filtered)}


@router.post("/live-check/alerts/triage")
async def admin_alert_triage(request: Request):
    _require(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    data = await request.json()
    fields = {"language", "person_number", "platform", "url", "state", "reason"}
    if not isinstance(data, dict) or set(data) != fields:
        raise HTTPException(422, "Invalid triage request")
    from .social_alerts import latest_alerts
    from .social_alert_triage import save_decision
    try:
        if not isinstance(data["language"], str) or data["language"] not in ("DE", "FR", "IT", "RM"):
            raise ValueError("Invalid language")
        current = latest_alerts(data["language"])
        if not any(item["person_number"] == data["person_number"] and
                   item["platform"] == data["platform"] and item["url"] == data["url"]
                   for item in current["alerts"]):
            raise HTTPException(404, "Current alert not found")
        result = save_decision(**data)
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid triage values")
    return {"ok": True, **result}


@router.get("/live-check/alerts/events")
async def admin_alert_events(request: Request, language: str, person_number: str,
                             platform: str, url: str):
    _require(request)
    from .social_alert_triage import events
    try:
        return {"events": events(language, person_number, platform, url)}
    except ValueError:
        raise HTTPException(422, "Invalid alert identity")


@router.get("/live-check/metrics")
async def admin_alert_metrics(request: Request, language: str = "DE", limit: int = 10):
    _require(request)
    from .social_alert_metrics import alert_metrics
    try:
        return alert_metrics(language, limit)
    except ValueError:
        raise HTTPException(422, "Invalid metrics query")


@router.get("/live-check/alerts/export.csv")
async def admin_alerts_csv(request: Request, language: str = "DE", search: str = "",
                           platform: str = "", severity: str = "all", state: str = "all",
                           date_from: str = "", date_to: str = "", new_only: bool = False):
    _require(request)
    from fastapi.responses import Response
    from .social_csv_export import export_alerts
    try:
        content = export_alerts(language, search=search, platform=platform, severity=severity,
                                state=state, date_from=date_from, date_to=date_to, new_only=new_only)
    except ValueError:
        raise HTTPException(422, "Invalid export language")
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="social-alerts-{language}.csv"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/live-check/metrics/export.csv")
async def admin_metrics_csv(request: Request, language: str = "DE", limit: int = 10):
    _require(request)
    from fastapi.responses import Response
    from .social_csv_export import export_trends
    try:
        content = export_trends(language, limit)
    except ValueError:
        raise HTTPException(422, "Invalid export query")
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="social-trends-{language}.csv"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/live-check/archive")
async def admin_alert_archive(request: Request, language: str = "DE", limit: int = 30,
                              search: str = "", platform: str = "", severity: str = "all",
                              date_from: str = "", date_to: str = "", new_only: bool = False):
    _require(request)
    from .social_alert_archive import archived_alerts
    try:
        return archived_alerts(language, limit=limit, search=search, platform=platform,
                               severity=severity, date_from=date_from, date_to=date_to,
                               new_only=new_only)
    except ValueError:
        raise HTTPException(422, "Invalid archive query")


@router.get("/live-check/archive/export.csv")
async def admin_alert_archive_csv(request: Request, language: str = "DE", limit: int = 30,
                                  search: str = "", platform: str = "", severity: str = "all",
                                  date_from: str = "", date_to: str = "", new_only: bool = False):
    _require(request)
    from fastapi.responses import Response
    from .social_csv_export import export_archive
    try:
        content = export_archive(language, limit=limit, search=search, platform=platform,
                                 severity=severity, date_from=date_from, date_to=date_to,
                                 new_only=new_only)
    except ValueError:
        raise HTTPException(422, "Invalid archive query")
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="social-archive-{language}.csv"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/live-check/facebook-legacy")
async def admin_facebook_legacy(request: Request, language: str = "DE"):
    _require(request)
    from .social_legacy_triage import legacy_candidates
    try:
        return legacy_candidates(language)
    except ValueError:
        raise HTTPException(422, "Invalid language")


@router.post("/live-check/facebook-legacy/review")
async def admin_facebook_legacy_review(request: Request):
    _require(request)
    if not _same_origin(request):
        raise HTTPException(403, "Same-origin request required")
    data = await request.json()
    expected = {"language", "person_number", "platform", "legacy_url",
                "target_url", "state", "reason"}
    if not isinstance(data, dict) or set(data) != expected:
        raise HTTPException(422, "Invalid legacy review")
    from .social_legacy_triage import resolve_legacy
    try:
        return resolve_legacy(**data)
    except ValueError:
        raise HTTPException(422, "Invalid legacy review or target conflict")


@router.get("/data-health")
async def admin_data_health(request: Request, language: str = "DE", stale_hours: int = 48):
    _require(request)
    from .social_data_health import data_health
    try:
        return data_health(language, stale_hours=stale_hours)
    except ValueError:
        raise HTTPException(422, "Invalid data health query")


@router.get("/data-health/trends")
async def admin_data_health_trends(request: Request, language: str = "DE", limit: int = 10):
    _require(request)
    from .social_quality_trends import quality_trends
    try:
        return quality_trends(language, limit=limit)
    except ValueError:
        raise HTTPException(422, "Invalid quality trend query")


@router.get("/data-health/regressions")
async def admin_data_health_regressions(
    request: Request, language: str = "DE", limit: int = 10,
    reachability_drop: int = 10, flagged_rise: int = 10,
    duplicate_increase: int = 1, invalid_increase: int = 1,
):
    _require(request)
    from .social_quality_regressions import quality_regressions
    try:
        return quality_regressions(
            language, limit=limit, reachability_drop=reachability_drop,
            flagged_rise=flagged_rise, duplicate_increase=duplicate_increase,
            invalid_increase=invalid_increase)
    except ValueError:
        raise HTTPException(422, "Invalid regression query")


@router.get("/social-discovery/worklist")
async def admin_social_discovery_worklist(request: Request, language: str = "DE",
                                          limit: int = 50, platform: str = "all"):
    _require(request)
    from .social_discovery_worklist import discovery_worklist
    try:
        return discovery_worklist(language, limit=limit, platform=platform)
    except ValueError:
        raise HTTPException(422, "Invalid social discovery query")
