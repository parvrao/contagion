"""Thin Claude wrapper over the Messages API (plain httpx, no SDK)."""
from __future__ import annotations

import json
import os
import re

from . import http
from .config import settings

API_VERSION = "2023-06-01"
WEB_SEARCH_TOOL = os.environ.get("ANTHROPIC_WEB_SEARCH_TOOL", "web_search_20250305")


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(settings.anthropic_api_key)


async def complete(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    web_search: bool = False,
    max_searches: int = 5,
    temperature: float = 0.0,
) -> tuple[str, list[dict]]:
    """Return (text, citations). Citations are [{url, title}] from web search."""
    if not available():
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")

    payload: dict = {
        "model": settings.anthropic_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if web_search:
        payload["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}]

    data = await http.post_json(
        f"{settings.anthropic_base_url.rstrip('/')}/v1/messages",
        payload,
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
        timeout=120.0,
    )

    text_parts: list[str] = []
    cited: list[dict] = []
    searched: list[dict] = []
    for block in data.get("content", []):
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
            for c in block.get("citations") or []:
                if c.get("url"):
                    cited.append({"url": c["url"], "title": c.get("title", "")})
        elif btype == "web_search_tool_result":
            content = block.get("content")
            if isinstance(content, list):
                for r in content:
                    if r.get("url"):
                        searched.append({"url": r["url"], "title": r.get("title", ""), "page_age": r.get("page_age", "")})

    # Prefer what the answer actually cited; fall back to what it looked at.
    citations = _dedupe(cited) or _dedupe(searched)
    return "".join(text_parts).strip(), citations


async def search_results(system: str, user: str, *, max_searches: int = 5) -> tuple[str, list[dict]]:
    """Run a web-search turn and return every result the model saw (not only cited ones)."""
    if not available():
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    payload = {
        "model": settings.anthropic_model,
        "max_tokens": 1200,
        "temperature": 0.0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}],
    }
    data = await http.post_json(
        f"{settings.anthropic_base_url.rstrip('/')}/v1/messages",
        payload,
        headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": API_VERSION},
        timeout=150.0,
    )
    results, text = [], []
    for block in data.get("content", []):
        if block.get("type") == "web_search_tool_result" and isinstance(block.get("content"), list):
            for r in block["content"]:
                if r.get("url"):
                    results.append({"url": r["url"], "title": r.get("title", ""), "page_age": r.get("page_age", "")})
        elif block.get("type") == "text":
            text.append(block.get("text", ""))
    return "".join(text), _dedupe(results)


async def complete_json(system: str, user: str, *, max_tokens: int = 3000):
    text, _ = await complete(
        system + "\n\nRespond with JSON only. No prose, no markdown fences.",
        user,
        max_tokens=max_tokens,
    )
    return parse_json(text)


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Model did not return valid JSON: {text[:200]}")


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = r["url"].split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
