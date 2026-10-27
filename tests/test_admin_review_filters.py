"""Review dashboard assets and filters remain available to administrators."""
import asyncio

import httpx
from app.main import app


def test_review_dashboard_contains_search_filter_and_statistics():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://example.test") as client:
            html=(await client.get("/admin")).text
            for element in ("review-search", "review-status", "review-platform",
                            "review-sort", "review-stats", "review-count"):
                assert 'id="'+element+'"' in html
            script=await client.get("/admin/review-ui.js")
            assert script.status_code == 200
            assert "reviewFiltered" in script.text
            assert "reviewStatistics" in script.text
            assert "textContent" in script.text
            assert "innerHTML" not in script.text
    asyncio.run(run())
