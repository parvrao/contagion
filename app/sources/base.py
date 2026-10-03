"""Source connector contract.

Every connector takes the same search plan and returns Items. A connector that
is missing credentials reports `skipped`, it never crashes the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..models import Item


@dataclass
class SearchPlan:
    brand: str
    claim: str
    queries: list[str]
    since: str = ""   # YYYY-MM-DD
    until: str = ""
    limit: int = 40
    extra: dict = field(default_factory=dict)

    def since_ts(self) -> int | None:
        return _ts(self.since)

    def until_ts(self) -> int | None:
        return _ts(self.until, end_of_day=True)


class Source:
    name = "base"
    platform = "Web"

    def enabled(self) -> tuple[bool, str]:
        return True, ""

    async def search(self, plan: SearchPlan) -> list[Item]:  # pragma: no cover
        raise NotImplementedError


def _ts(day: str, end_of_day: bool = False) -> int | None:
    if not day:
        return None
    try:
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp()) + (86399 if end_of_day else 0)


def iso_from_ts(ts: float | int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


PLATFORM_BY_DOMAIN = {
    "reddit.com": "Reddit",
    "old.reddit.com": "Reddit",
    "news.ycombinator.com": "Hacker News",
    "x.com": "X",
    "twitter.com": "X",
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "bsky.app": "Bluesky",
    "linkedin.com": "LinkedIn",
    "medium.com": "Blogs",
    "substack.com": "Blogs",
    "quora.com": "Q&A",
    "wikipedia.org": "Wikipedia",
}


def platform_for(url: str, default: str = "News & web") -> str:
    d = domain_of(url)
    for key, label in PLATFORM_BY_DOMAIN.items():
        if d == key or d.endswith("." + key):
            return label
    return default


def in_window(published_iso: str, plan: SearchPlan) -> bool:
    """Undated items are kept: dropping them would hide evidence."""
    if not published_iso:
        return True
    try:
        ts = datetime.fromisoformat(published_iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return True
    lo, hi = plan.since_ts(), plan.until_ts()
    if lo and ts < lo:
        return False
    if hi and ts > hi:
        return False
    return True
