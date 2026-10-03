"""YouTube Data API v3 search. Optional: needs YOUTUBE_API_KEY."""
from __future__ import annotations

from .. import http
from ..config import settings
from ..models import Item
from .base import SearchPlan, Source, in_window


class YouTubeSource(Source):
    name = "youtube"
    platform = "YouTube"

    def enabled(self) -> tuple[bool, str]:
        return (True, "") if settings.youtube_api_key else (False, "set YOUTUBE_API_KEY to enable")

    async def search(self, plan: SearchPlan) -> list[Item]:
        items: dict[str, Item] = {}
        for q in plan.queries[:2]:
            params = {
                "part": "snippet", "q": q, "type": "video",
                "maxResults": min(plan.limit, 25), "key": settings.youtube_api_key,
            }
            if plan.since:
                params["publishedAfter"] = f"{plan.since}T00:00:00Z"
            if plan.until:
                params["publishedBefore"] = f"{plan.until}T23:59:59Z"
            data = await http.get_json("https://www.googleapis.com/youtube/v3/search", params=params)
            for v in data.get("items", []):
                vid = v.get("id", {}).get("videoId")
                if not vid:
                    continue
                s = v.get("snippet", {})
                url = f"https://www.youtube.com/watch?v={vid}"
                if url in items or not in_window(s.get("publishedAt", ""), plan):
                    continue
                items[url] = Item(
                    platform="YouTube", url=url, domain="youtube.com",
                    title=s.get("title", ""), text=s.get("description", "")[:600],
                    author=s.get("channelTitle", ""), published_at=s.get("publishedAt", ""), query=q,
                )
        return list(items.values())
