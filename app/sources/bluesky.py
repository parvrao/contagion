"""Bluesky public search (AT Protocol AppView). No key for public search today;
if Bluesky starts requiring auth the source reports an error and the run continues."""
from __future__ import annotations

from .. import http
from ..models import Item
from .base import SearchPlan, Source, in_window


class BlueskySource(Source):
    name = "bluesky"
    platform = "Bluesky"

    async def search(self, plan: SearchPlan) -> list[Item]:
        items: dict[str, Item] = {}
        for q in plan.queries[:3]:
            params = {"q": q, "limit": min(plan.limit, 100), "sort": "top"}
            if plan.since:
                params["since"] = f"{plan.since}T00:00:00Z"
            if plan.until:
                params["until"] = f"{plan.until}T23:59:59Z"
            data = await http.get_json("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts", params=params)
            for p in data.get("posts", []):
                handle = p.get("author", {}).get("handle", "")
                rkey = p.get("uri", "").rsplit("/", 1)[-1]
                url = f"https://bsky.app/profile/{handle}/post/{rkey}"
                record = p.get("record", {})
                published = record.get("createdAt", "")
                if url in items or not in_window(published, plan):
                    continue
                links = []
                embed = (record.get("embed") or {}).get("external") or {}
                if embed.get("uri"):
                    links.append(embed["uri"])
                items[url] = Item(
                    platform="Bluesky",
                    url=url,
                    domain="bsky.app",
                    title="",
                    text=(record.get("text") or "")[:1000],
                    author="@" + handle,
                    published_at=published,
                    engagement=int(p.get("likeCount", 0)) + int(p.get("repostCount", 0)) + int(p.get("replyCount", 0)),
                    outbound_links=links,
                    query=q,
                )
        return list(items.values())
