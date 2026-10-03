"""GDELT DOC 2.0: free, keyless, near-real-time index of world news (updates every 15 min).
Works from datacenter IPs, which is why it anchors the live Watch."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .. import http
from ..models import Item
from .base import SearchPlan, Source, in_window


def _iso(seen: str) -> str:
    try:
        return datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


class GdeltSource(Source):
    name = "gdelt"
    platform = "News"

    async def search(self, plan: SearchPlan) -> list[Item]:
        days = max(1, min(7, plan.extra.get("days", 7)))
        items: dict[str, Item] = {}
        for i, q in enumerate(plan.extra.get("gdelt_queries") or plan.queries[:2]):
            if i:
                await asyncio.sleep(5.5)   # GDELT asks for one request every 5 seconds
            resp = await http.request("GET", "https://api.gdeltproject.org/api/v2/doc/doc", params={
                "query": f"{q} sourcelang:english", "mode": "ArtList", "format": "json",
                "maxrecords": min(plan.limit * 2, 75), "timespan": f"{days}d", "sort": "DateDesc"}, timeout=40.0)
            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError(f"GDELT: {resp.text[:120]}")
            for a in data.get("articles", []):
                url = a.get("url", "")
                if not url or url in items:
                    continue
                published = _iso(a.get("seendate", ""))
                if not in_window(published, plan):
                    continue
                items[url] = Item(platform="News", url=url, domain=a.get("domain", ""), title=a.get("title", "")[:300],
                                  author=a.get("domain", ""), published_at=published, query=q, date_source="index")
        return list(items.values())
