"""AI answer engines we ask about the rumor.

Each engine answers the same neutral questions. Engines without a key are
skipped and listed as such in the UI, never silently dropped.
"""
from __future__ import annotations

import os

from .. import http, llm
from ..config import settings

NEUTRAL_SYSTEM = (
    "Answer the user's question accurately and concisely (under 150 words). "
    "If you are unsure, say so."
)


class Engine:
    name = "engine"
    mode = "live web"

    def enabled(self) -> tuple[bool, str]:
        return True, ""

    async def ask(self, question: str) -> tuple[str, list[dict]]:  # pragma: no cover
        raise NotImplementedError


class ClaudeLive(Engine):
    name, mode = "Claude", "live web"

    def enabled(self):
        return (True, "") if llm.available() else (False, "needs ANTHROPIC_API_KEY")

    async def ask(self, question):
        return await llm.complete(NEUTRAL_SYSTEM, question, max_tokens=600, web_search=True, max_searches=3)


class ClaudeMemory(Engine):
    """No tools: shows whether the rumor is baked into what the model already 'knows'."""
    name, mode = "Claude", "model memory"

    def enabled(self):
        return (True, "") if llm.available() else (False, "needs ANTHROPIC_API_KEY")

    async def ask(self, question):
        return await llm.complete(NEUTRAL_SYSTEM, question, max_tokens=500)


class OpenAILive(Engine):
    name, mode = "ChatGPT (API)", "live web"

    def enabled(self):
        return (True, "") if settings.openai_api_key else (False, "set OPENAI_API_KEY to enable")

    async def ask(self, question):
        data = await http.post_json(
            "https://api.openai.com/v1/responses",
            {
                "model": settings.openai_model,
                "instructions": NEUTRAL_SYSTEM,
                "input": question,
                "tools": [{"type": os.environ.get("OPENAI_WEB_TOOL", "web_search")}],
            },
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=120.0,
        )
        text, cites = [], []
        for out in data.get("output", []):
            if out.get("type") != "message":
                continue
            for part in out.get("content", []):
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                    for a in part.get("annotations", []):
                        if a.get("type") == "url_citation" and a.get("url"):
                            cites.append({"url": a["url"], "title": a.get("title", "")})
        return "".join(text).strip(), cites


class PerplexityLive(Engine):
    name, mode = "Perplexity", "live web"

    def enabled(self):
        return (True, "") if settings.perplexity_api_key else (False, "set PERPLEXITY_API_KEY to enable")

    async def ask(self, question):
        data = await http.post_json(
            "https://api.perplexity.ai/chat/completions",
            {
                "model": settings.perplexity_model,
                "messages": [
                    {"role": "system", "content": NEUTRAL_SYSTEM},
                    {"role": "user", "content": question},
                ],
            },
            headers={"Authorization": f"Bearer {settings.perplexity_api_key}"},
            timeout=90.0,
        )
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        urls = [r.get("url") for r in data.get("search_results", []) if r.get("url")] or data.get("citations", [])
        return text.strip(), [{"url": u, "title": ""} for u in urls if isinstance(u, str)]


class GeminiLive(Engine):
    name, mode = "Gemini", "live web"

    def enabled(self):
        return (True, "") if settings.gemini_api_key else (False, "set GEMINI_API_KEY to enable")

    async def ask(self, question):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
        data = await http.post_json(
            url,
            {
                "systemInstruction": {"parts": [{"text": NEUTRAL_SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": question}]}],
                "tools": [{"google_search": {}}],
            },
            headers={"x-goog-api-key": settings.gemini_api_key},
            timeout=90.0,
        )
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        cites = []
        for chunk in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
            web = chunk.get("web") or {}
            if web.get("uri"):
                # Gemini returns redirect URIs; the title holds the publisher domain.
                cites.append({"url": web["uri"], "title": web.get("title", "")})
        return text.strip(), cites


def all_engines() -> list[Engine]:
    return [ClaudeLive(), ClaudeMemory(), OpenAILive(), PerplexityLive(), GeminiLive()]
