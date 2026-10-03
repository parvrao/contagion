"""Hacker News via the public Algolia API (no key, generous limits)."""
from __future__ import annotations

from .. import http
from ..models import Item
from .base import SearchPlan, Source, in_window


class HackerNewsSource(Source):
    name = "hackernews"
    platform = "Hacker News"

    async def search(self, plan: SearchPlan) -> list[Item]:
        numeric = []
        if plan.since_ts():
            numeric.append(f"created_at_i>{plan.since_ts()}")
        if plan.until_ts():
            numeric.append(f"created_at_i<{plan.until_ts()}")

        items: dict[str, Item] = {}
        for q in plan.queries[:3]:
            params = {"query": q, "tags": "(story,comment)", "hitsPerPage": min(plan.limit, 50)}
            if numeric:
                params["numericFilters"] = ",".join(numeric)
            data = await http.get_json("https://hn.algolia.com/api/v1/search", params=params)
            for h in data.get("hits", []):
                url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                if url in items or not in_window(h.get("created_at", ""), plan):
                    continue
                is_comment = "comment" in (h.get("_tags") or [])
                items[url] = Item(
                    platform="Hacker News",
                    url=url,
                    domain="news.ycombinator.com",
                    title=h.get("title") or h.get("story_title") or "",
                    text=(h.get("comment_text") or h.get("story_text") or "")[:1200],
                    author=h.get("author", ""),
                    published_at=h.get("created_at", ""),
                    engagement=int(h.get("points") or 0) + int(h.get("num_comments") or 0),
                    outbound_links=[h["url"]] if h.get("url") and not is_comment else [],
                    query=q,
                )
        return list(items.values())
