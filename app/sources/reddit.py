"""Reddit search.

Uses app-only OAuth when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set
(higher, documented rate limits). Falls back to the public .json endpoint,
which Reddit may throttle or block; that shows up as an error on the source,
not a crashed run.
"""
from __future__ import annotations

import os
import time

from .. import http
from ..models import Item
from .base import SearchPlan, Source, in_window, iso_from_ts

_token: dict = {"value": "", "expires": 0.0}


async def _oauth_token() -> str:
    cid, secret = os.environ.get("REDDIT_CLIENT_ID", ""), os.environ.get("REDDIT_CLIENT_SECRET", "")
    if not (cid and secret):
        return ""
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    resp = await http.request(
        "POST",
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        auth=(cid, secret),
    )
    data = resp.json()
    _token.update(value=data["access_token"], expires=time.time() + float(data.get("expires_in", 3600)))
    return _token["value"]


class RedditSource(Source):
    name = "reddit"
    platform = "Reddit"

    def enabled(self) -> tuple[bool, str]:
        # Reddit blocks unauthenticated requests from cloud servers (403); the open-web sweep still finds threads.
        if os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"):
            return True, ""
        return False, "needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (Reddit blocks anonymous server requests)"

    async def search(self, plan: SearchPlan) -> list[Item]:
        token = await _oauth_token()
        base = "https://oauth.reddit.com/search" if token else "https://www.reddit.com/search.json"
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        items: dict[str, Item] = {}
        for q in plan.queries[:4]:
            data = await http.get_json(
                base,
                params={"q": q, "sort": "relevance", "t": "all", "limit": min(plan.limit, 50), "type": "link"},
                headers=headers,
            )
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                permalink = "https://www.reddit.com" + d.get("permalink", "")
                if permalink in items:
                    continue
                published = iso_from_ts(d.get("created_utc"))
                if not in_window(published, plan):
                    continue
                outbound = []
                if not d.get("is_self") and d.get("url") and "reddit.com" not in d.get("url", ""):
                    outbound.append(d["url"])
                items[permalink] = Item(
                    platform="Reddit",
                    url=permalink,
                    domain="reddit.com",
                    title=d.get("title", ""),
                    text=(d.get("selftext") or "")[:1200],
                    author=f"u/{d.get('author', '?')} in r/{d.get('subreddit', '?')}",
                    published_at=published,
                    engagement=int(d.get("score", 0)) + int(d.get("num_comments", 0)),
                    outbound_links=outbound,
                    query=q,
                )
        return list(items.values())
