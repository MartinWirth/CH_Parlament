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


def _now():
    return datetime.now(timezone.utc).isoformat()


def _same_origin(request):
    origin = request.headers.get("origin")
    # Require Origin and a custom header for state-changing browser requests.
    return bool(origin and origin.rstrip("/") == str(request.base_url).rstrip("/")
                and request.headers.get("X-Admin-Action") == "1")


def _authorized(request):
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
    recent = [t for t in _login_failures.get(client_id, []) if time.monotonic() - t < 300]
    _login_failures[client_id] = recent
    if len(recent) >= 5:
        raise HTTPException(429, "Too many login attempts; retry later")
    data = await request.json()
    password = data.get("password", "") if isinstance(data, dict) else ""
    if not isinstance(password, str) or not secrets.compare_digest(password, configured):
        recent.append(time.monotonic())
        raise HTTPException(403, "Invalid credentials")
    _login_failures.pop(client_id, None)
    from fastapi.responses import JSONResponse
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.monotonic() + SESSION_SECONDS
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
async def admin_live_check_alerts(request: Request, language: str = "DE", search: str = "",\n                                  platform: str = "", severity: str = "all", state: str = "all",\n                                  date_from: str = "", date_to: str = "", new_only: bool = False):
    _require(request)
    if language not in ("DE", "FR", "IT", "RM"):
        raise HTTPException(422, "Invalid language")
    from .social_alerts import latest_alerts
    return latest_alerts(language)


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
async def admin_alerts_csv(request: Request, language: str = "DE", search: str = "",\n                           platform: str = "", severity: str = "all", state: str = "all",\n                           date_from: str = "", date_to: str = "", new_only: bool = False):
    _require(request)
    from fastapi.responses import Response
    from .social_csv_export import export_alerts
    try:
        content = export_alerts(language, search=search, platform=platform, severity=severity,\n                                state=state, date_from=date_from, date_to=date_to, new_only=new_only)
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
