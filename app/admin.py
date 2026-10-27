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
    data = await request.json()
    password = data.get("password", "") if isinstance(data, dict) else ""
    if not isinstance(password, str) or not secrets.compare_digest(password, configured):
        raise HTTPException(403, "Invalid credentials")
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
