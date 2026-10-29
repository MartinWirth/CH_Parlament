"""Fail CI when the documented endpoint inventory drifts from FastAPI routes.

Checks exact HTTP method + path pairs, including public and admin routes.
FastAPI's built-in OpenAPI and documentation routes are documented but omitted
from app.routes; those are explicitly accounted for below.
"""
import re
from pathlib import Path

from app.main import app


DOC = Path(__file__).resolve().parents[1] / "docs" / "use-cases-and-endpoints.md"
TABLE_ROW = re.compile(r"^\|\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\|\s*`([^\`]+)`\s*\|")
BUILTIN_ROUTES = {
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/docs/oauth2-redirect"),
}


def documented_endpoints():
    found = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = TABLE_ROW.match(line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def actual_endpoints():
    # OpenAPI is the authoritative published API surface. Include admin
    # endpoints even if routers were added after FastAPI initialization.
    return {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    }


def test_endpoint_inventory_matches_fastapi_routes():
    documented = documented_endpoints()
    assert len(documented) == len(set(documented)), "Duplicate endpoint rows in documentation"
    # The framework registers HEAD implicitly on its docs routes; document GET.\n    # The admin router uses a prefix that is already present in route.path.\n    expected = actual_endpoints() | BUILTIN_ROUTES
    missing = expected - set(documented)
    obsolete = set(documented) - expected
    assert not missing and not obsolete, (
        f"Undocumented endpoints: {sorted(missing)}; "
        f"Documented endpoints absent from FastAPI: {sorted(obsolete)}"
    )


def test_each_endpoint_is_in_public_or_admin_section():
    text = DOC.read_text(encoding="utf-8")
    public, admin = text.split("## Öffentliche Endpunkte", 1)[1].split("## Admin-Endpunkte", 1)
    admin = admin.split("## Typische Abläufe", 1)[0]
    for method, path in documented_endpoints():
        section = admin if path.startswith("/admin") else public
        assert f"| {method} | `{path}` |" in section, (method, path)
