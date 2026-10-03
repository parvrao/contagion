"""GDELT DOC 2.0: free, keyless, near-real-time index of world news (updates every 15 min).
Works from datacenter IPs, which is why it anchors the live Watch."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .. import http
from ..models import Item
from .base import SearchPlan, Source, in_window


def _iso(seen: str) -> str:
    try:
        return datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


# GDELT allows one request every 5 seconds per IP. Every GDELT call in the process
# (all watches, backfills, counter runs) goes through one gate, and results are
# cached because the index only refreshes every 15 minutes anyway.
MIN_GAP = 6.0
CACHE_TTL = 600.0
BACKOFF_429 = 10.0
_gate = asyncio.Lock()
_last_call = 0.0
_cache: dict[tuple, tuple[float, dict]] = {}


class RateLimited(RuntimeError):
    pass


async def _fetch(params: dict) -> dict:
    global _last_call
    key = tuple(sorted(params.items()))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    async with _gate:
        for attempt in range(2):
            wait = _last_call + MIN_GAP - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            _last_call = time.monotonic()
            try:
                resp = await http.request("GET", "https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=40.0, attempts=1)
            except http.UpstreamError as exc:
                if exc.status == 429 and attempt == 0:
                    _last_call = time.monotonic() + BACKOFF_429   # back off harder, then one more try
                    continue
                if exc.status == 429:
                    if hit:
                        return hit[1]                    # stale beats nothing
                    raise RateLimited("rate-limited") from exc
                raise
            try:
                data = resp.json()
            except ValueError:
                raise RuntimeError(f"GDELT: {resp.text[:120]}")
            _cache[key] = (time.monotonic(), data)
            return data
    raise RateLimited("rate-limited")


class GdeltSource(Source):
    name = "gdelt"
    platform = "News"

    async def search(self, plan: SearchPlan) -> list[Item]:
        days = max(1, min(7, plan.extra.get("days", 7)))
        items: dict[str, Item] = {}
        queries = plan.extra.get("gdelt_queries") or plan.queries[:2]
        limited = 0
        for q in queries:
            try:
                data = await _fetch({"query": f"{q} sourcelang:english", "mode": "ArtList", "format": "json",
                                     "maxrecords": min(plan.limit * 2, 75), "timespan": f"{days}d", "sort": "DateDesc"})
            except RateLimited:
                limited += 1
                continue
            for a in data.get("articles", []):
                url = a.get("url", "")
                if not url or url in items:
                    continue
                published = _iso(a.get("seendate", ""))
                if not in_window(published, plan):
                    continue
                items[url] = Item(platform="News", url=url, domain=a.get("domain", ""), title=a.get("title", "")[:300],
                                  author=a.get("domain", ""), published_at=published, query=q, date_source="index")
        if limited and not items:
            raise RuntimeError("GDELT is rate-limiting this server (1 request per 5 s per IP); skipped this cycle, retrying next poll")
        return list(items.values())
