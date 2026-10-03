"""Open-web sweep through Claude's web search tool.

This is how we reach platforms that have no open search API (TikTok, X,
Instagram, Facebook, Threads, forums, blogs): we search the public, indexed
pages. Each sweep asks Claude to report what each page says, so the analysis
stage has more than a bare title to work with.
"""
from __future__ import annotations

import asyncio

from .. import llm
from ..models import Item
from .base import SearchPlan, Source, domain_of, in_window, platform_for

SWEEPS = [
    ("social", "Find public posts and videos on TikTok, X/Twitter, Instagram, Facebook and Threads that repeat or discuss this claim."),
    ("forums", "Find forum threads, Q&A pages (Quora, Reddit), blogs and Substack/Medium posts that repeat or discuss this claim."),
    ("news", "Find the earliest news articles and blog posts that reported this claim, and any later fact checks or brand statements."),
]

SYSTEM = (
    "You are a research assistant tracing how a claim about a brand spread online. "
    "Search the web, then report ONLY pages you actually found in search results. Never invent URLs. "
    "Return a JSON array; each element: "
    '{"url": str, "title": str, "published": "YYYY-MM-DD or empty", "author": "public handle or outlet, or empty", '
    '"summary": "one sentence: what this page says about the claim"}. '
    "Up to 15 elements. JSON only."
)


class WebSearchSource(Source):
    name = "web"
    platform = "Web"

    def enabled(self) -> tuple[bool, str]:
        return (True, "") if llm.available() else (False, "needs ANTHROPIC_API_KEY or GEMINI_API_KEY")

    async def search(self, plan: SearchPlan) -> list[Item]:
        window = ""
        if plan.since or plan.until:
            window = f"\nFocus on pages from {plan.since or 'any time'} to {plan.until or 'now'}."
        prompts = [
            f"Brand: {plan.brand}\nClaim: \"{plan.claim}\"\nSearch phrases people use: {', '.join(plan.queries[:5])}\n{instruction}{window}"
            for _, instruction in SWEEPS
        ]
        results = await asyncio.gather(
            *[llm.search_results(SYSTEM, p, max_searches=4) for p in prompts], return_exceptions=True
        )

        items: dict[str, Item] = {}
        errors = []
        for (sweep, _), res in zip(SWEEPS, results):
            if isinstance(res, Exception):
                errors.append(f"{sweep}: {res}")
                continue
            text, raw = res
            seen_urls = {r["url"].rstrip("/") for r in raw}
            described = []
            try:
                parsed = llm.parse_json(text)
                described = parsed if isinstance(parsed, list) else []
            except ValueError:
                described = []
            # Described rows: only keep URLs that really came back from search.
            for row in described:
                url = str(row.get("url", "")).strip()
                if not url or url.rstrip("/") not in seen_urls:
                    continue
                published = _iso(row.get("published", ""))
                if not in_window(published, plan):
                    continue
                items[url] = Item(
                    platform=platform_for(url), url=url, domain=domain_of(url),
                    title=str(row.get("title", ""))[:300], text=str(row.get("summary", ""))[:600],
                    author=str(row.get("author", ""))[:120], published_at=published, query=f"web:{sweep}",
                    date_source="model-reported",
                )
            for r in raw:
                url = r["url"]
                if url in items:
                    continue
                items[url] = Item(
                    platform=platform_for(url), url=url, domain=domain_of(url), title=r.get("title", "")[:300],
                    published_at=_iso(r.get("page_age", "")), query=f"web:{sweep}", date_source="search index",
                )
        if errors and not items:
            raise RuntimeError("; ".join(errors))
        return list(items.values())


def _iso(value: str) -> str:
    """Accept YYYY-MM-DD or loose strings like 'March 1, 2024'; return ISO or ''."""
    from datetime import datetime, timezone

    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:30], fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""
