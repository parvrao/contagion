"""Google News RSS search (no key). Supports after:/before: date operators."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .. import http
from ..models import Item
from .base import SearchPlan, Source, domain_of, in_window

_TAG = re.compile(r"<[^>]+>")


class GoogleNewsSource(Source):
    name = "google_news"
    platform = "News"

    async def search(self, plan: SearchPlan) -> list[Item]:
        items: dict[str, Item] = {}
        for q in plan.queries[:4]:
            query = q
            if plan.since:
                query += f" after:{plan.since}"
            if plan.until:
                query += f" before:{plan.until}"
            resp = await http.request(
                "GET",
                "https://news.google.com/rss/search",
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            )
            for item in parse_rss(resp.text)[: plan.limit]:
                if item.url in items or not in_window(item.published_at, plan):
                    continue
                item.query = q
                items[item.url] = item
        return list(items.values())


def parse_rss(xml_text: str) -> list[Item]:
    out: list[Item] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        src = node.find("source")
        outlet = (src.text or "").strip() if src is not None else ""
        outlet_url = src.get("url", "") if src is not None else ""
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(outlet) - 3]
        published = ""
        if node.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(node.findtext("pubDate")).isoformat(timespec="seconds")
            except (TypeError, ValueError):
                published = ""
        desc = html.unescape(_TAG.sub(" ", node.findtext("description") or "")).strip()
        out.append(
            Item(
                platform="News",
                url=link,
                domain=domain_of(outlet_url) if outlet_url else domain_of(link),
                title=title,
                text=desc[:600],
                author=outlet,
                published_at=published,
            )
        )
    return out
