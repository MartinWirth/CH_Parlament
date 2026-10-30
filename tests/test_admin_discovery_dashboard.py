from pathlib import Path

from app.main import app


def test_discovery_dashboard_route_and_script_are_present():
    html = (Path(__file__).resolve().parents[1] / "app/static/admin.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "app/static/admin-discovery.js").read_text(encoding="utf-8")
    assert 'id="coverage-cards"' in html
    assert 'id="coverage-candidates"' in html
    assert 'src="/admin/discovery-ui.js"' in html
    assert 'discovery-report?' in js
    assert 'textContent' in js
    assert 'innerHTML' not in js
    assert "/admin/discovery-ui.js" in app.openapi()["paths"]
