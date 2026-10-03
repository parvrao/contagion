"""Profound cross-reference.

Question we ask Profound: across the prompts Profound already tracks for this
brand's category, how often do AI answers cite the pages that carry the rumor,
on which models, and since when?

Uses POST /v1/reports/citations (documented: metrics count / citation_share /
first_cited_at; dimensions url / hostname / model; filters on url / hostname).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import http
from ..config import settings


def enabled() -> tuple[bool, str]:
    if not settings.profound_api_key:
        return False, "set PROFOUND_API_KEY to enable"
    if not settings.profound_category_id:
        return False, "set PROFOUND_CATEGORY_ID to enable"
    return True, ""


async def citations_for(hostnames: list[str], days: int = 90) -> list[dict]:
    """Return [{hostname, model, count, first_cited_at}] for the given rumor hostnames."""
    if not hostnames:
        return []
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    payload = {
        "category_id": settings.profound_category_id,
        "start_date": start.isoformat(timespec="seconds"),
        "end_date": end.isoformat(timespec="seconds"),
        "metrics": ["count", "first_cited_at"],
        "dimensions": ["hostname", "model"],
        "filters": [{"field": "hostname", "operator": "in", "value": hostnames[:50]}],
        "order_by": {"count": "desc"},
        "pagination": {"limit": 200, "offset": 0},
    }
    data = await http.post_json(
        f"{settings.profound_base_url.rstrip('/')}/v1/reports/citations",
        payload,
        headers={"X-API-Key": settings.profound_api_key},
        timeout=60.0,
    )
    rows = []
    for row in data.get("data", []):
        dims, mets = row.get("dimensions", []), row.get("metrics", [])
        rows.append(
            {
                "hostname": dims[0] if dims else "",
                "model": dims[1] if len(dims) > 1 else "",
                "count": mets[0] if mets else 0,
                "first_cited_at": mets[1] if len(mets) > 1 else "",
            }
        )
    return rows
