"""Regression tests for bounded, expiring admin authentication state."""
import time
from app import admin


def test_prune_removes_expired_sessions_and_old_failures(monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(admin, "_sessions", {"expired": now - 1, "valid": now + 100})
    monkeypatch.setattr(admin, "_login_failures", {"old": [now - 301], "mixed": [now - 301, now - 2]})
    admin._prune_auth_state(now)
    assert admin._sessions == {"valid": now + 100}
    assert admin._login_failures == {"mixed": [now - 2]}


def test_prune_bounds_sessions_and_clients(monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(admin, "MAX_ACTIVE_SESSIONS", 2)
    monkeypatch.setattr(admin, "MAX_TRACKED_CLIENTS", 2)
    monkeypatch.setattr(admin, "_sessions", {"a": now + 10, "b": now + 20, "c": now + 30})
    monkeypatch.setattr(admin, "_login_failures", {"a": [now - 3], "b": [now - 2], "c": [now - 1]})
    admin._prune_auth_state(now)
    assert set(admin._sessions) == {"b", "c"}
    assert set(admin._login_failures) == {"b", "c"}


def test_prune_keeps_recent_failure_limit(monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(admin, "_sessions", {})
    monkeypatch.setattr(admin, "_login_failures", {"client": [now - i for i in range(7)]})
    admin._prune_auth_state(now)
    assert len(admin._login_failures["client"]) == admin.MAX_LOGIN_FAILURES
