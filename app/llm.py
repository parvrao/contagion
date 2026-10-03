"""Thin Claude wrapper over the Messages API (plain httpx, no SDK)."""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager

from . import http
from .config import settings

# ---- model routing + response cache -------------------------------------------------
# `with llm.fast():` routes calls in that block to the fast model (falls back to the
# main model if the fast one is unavailable). `with llm.no_cache():` forces fresh calls
# (AI engine probes and re-checks must never be served from cache).
_fast = contextvars.ContextVar("contagion_fast", default=False)
_nocache = contextvars.ContextVar("contagion_nocache", default=False)
_fast_broken = {"v": False}
CACHE_ON = os.environ.get("CONTAGION_CACHE", "1") != "0"
CACHE_TTL = float(os.environ.get("CONTAGION_CACHE_TTL", str(6 * 3600)))
_mem: dict[str, tuple[float, object]] = {}


@contextmanager
def fast():
    tok = _fast.set(True)
    try:
        yield
    finally:
        _fast.reset(tok)


@contextmanager
def no_cache():
    tok = _nocache.set(True)
    try:
        yield
    finally:
        _nocache.reset(tok)


def _model() -> str:
    return settings.fast_model if (_fast.get() and not _fast_broken["v"] and settings.fast_model) else settings.anthropic_model


def _cache_dir():
    d = settings.data_dir / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ckey(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def cache_get(key: str):
    if not CACHE_ON or _nocache.get():
        return None
    hit = _mem.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    f = _cache_dir() / f"{key}.json"
    try:
        if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL:
            val = json.loads(f.read_text())
            _mem[key] = (time.time(), val)
            return val
    except (OSError, ValueError):
        return None
    return None


def cache_put(key: str, val) -> None:
    if not CACHE_ON:
        return
    _mem[key] = (time.time(), val)
    try:
        (_cache_dir() / f"{key}.json").write_text(json.dumps(val))
    except (OSError, TypeError):
        pass


def cache_stats() -> dict:
    return {"enabled": CACHE_ON, "memory_entries": len(_mem), "ttl_hours": CACHE_TTL / 3600}


async def _anthropic(payload: dict, timeout: float) -> dict:
    """POST /v1/messages; if the fast model is rejected, mark it broken and retry on the main model."""
    url = f"{settings.anthropic_base_url.rstrip('/')}/v1/messages"
    headers = {"x-api-key": settings.anthropic_api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    try:
        return await http.post_json(url, payload, headers=headers, timeout=timeout)
    except http.UpstreamError as exc:
        if payload.get("model") == settings.fast_model and exc.status in (400, 404) and "model" in str(exc).lower():
            _fast_broken["v"] = True
            return await http.post_json(url, {**payload, "model": settings.anthropic_model}, headers=headers, timeout=timeout)
        raise

API_VERSION = "2023-06-01"
WEB_SEARCH_TOOL = os.environ.get("ANTHROPIC_WEB_SEARCH_TOOL", "web_search_20250305")


class LLMUnavailable(RuntimeError):
    pass


def provider() -> str:
    """Claude when ANTHROPIC_API_KEY is set; otherwise Gemini (free tier) when GEMINI_API_KEY is set."""
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.gemini_api_key:
        return "gemini"
    return ""


def label() -> str:
    return "Gemini" if provider() == "gemini" else "Claude"


def available() -> bool:
    return bool(provider())


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


_gemini_ok: dict = {"model": ""}
last_error: dict = {"msg": ""}


def _gemini_models() -> list[str]:
    seen, out = set(), []
    for m in [_gemini_ok["model"], settings.gemini_model, "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-2.0-flash"]:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


async def _gemini(system: str, user: str, *, max_tokens: int, temperature: float, search: bool, json_mode: bool = False):
    body: dict = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max(max_tokens, 8192)},
    }
    if search:
        body["tools"] = [{"google_search": {}}]
    elif json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    data, err = None, None
    for model in _gemini_models():
        try:
            data = await http.post_json(
                GEMINI_URL.format(model=model), body,
                headers={"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"},
                timeout=60.0, attempts=2,   # fail fast: a stuck call used to hold the watch for up to 12 min
            )
            _gemini_ok["model"] = model
            break
        except http.UpstreamError as exc:
            err = exc
            # Model retired or not offered to this key: try the next one. Anything else (quota, auth) is final.
            if exc.status in (400, 404) and ("model" in str(exc).lower() or exc.status == 404):
                continue
            break
    if data is None:
        last_error["msg"] = f"Gemini: {str(err)[:200]}"
        raise err or RuntimeError("Gemini unavailable")
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
    if not text and cand.get("finishReason"):
        last_error["msg"] = f"Gemini returned no text (finishReason {cand.get('finishReason')})"
    results = []
    for chunk in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
        web = chunk.get("web") or {}
        if web.get("uri"):
            # Gemini returns redirect URIs; the title holds the publisher domain.
            results.append({"url": web["uri"], "title": web.get("title", ""), "page_age": ""})
    return text.strip(), _dedupe(results)


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
        raise LLMUnavailable("Set ANTHROPIC_API_KEY or GEMINI_API_KEY")
    key = _ckey("complete", provider(), _model(), system, user, max_tokens, web_search, max_searches, temperature)
    hit = cache_get(key)
    if hit is not None:
        return hit[0], hit[1]
    if provider() == "gemini":
        out = await _gemini(system, user, max_tokens=max_tokens, temperature=temperature, search=web_search)
        cache_put(key, list(out))
        return out

    payload: dict = {
        "model": _model(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if web_search:
        payload["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}]

    data = await _anthropic(payload, timeout=120.0)

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
    out = ("".join(text_parts).strip(), citations)
    if out[0]:
        cache_put(key, list(out))
    return out


async def search_results(system: str, user: str, *, max_searches: int = 5) -> tuple[str, list[dict]]:
    """Run a web-search turn and return every result the model saw (not only cited ones)."""
    if not available():
        raise LLMUnavailable("Set ANTHROPIC_API_KEY or GEMINI_API_KEY")
    key = _ckey("search", provider(), settings.anthropic_model, system, user, max_searches)
    hit = cache_get(key)
    if hit is not None:
        return hit[0], hit[1]
    if provider() == "gemini":
        out = await _gemini(system, user, max_tokens=1200, temperature=0.0, search=True)
        cache_put(key, list(out))
        return out
    payload = {
        "model": settings.anthropic_model,
        "max_tokens": 1200,
        "temperature": 0.0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}],
    }
    data = await _anthropic(payload, timeout=150.0)
    results, text = [], []
    for block in data.get("content", []):
        if block.get("type") == "web_search_tool_result" and isinstance(block.get("content"), list):
            for r in block["content"]:
                if r.get("url"):
                    results.append({"url": r["url"], "title": r.get("title", ""), "page_age": r.get("page_age", "")})
        elif block.get("type") == "text":
            text.append(block.get("text", ""))
    out = ("".join(text), _dedupe(results))
    if out[1]:
        cache_put(key, list(out))
    return out


async def complete_json(system: str, user: str, *, max_tokens: int = 3000):
    if provider() == "gemini":
        key = _ckey("gemini_json", system, user, max_tokens)
        text = cache_get(key)
        if text is None:
            text, _ = await _gemini(system + "\n\nRespond with JSON only.", user, max_tokens=max_tokens, temperature=0.0, search=False, json_mode=True)
            data = parse_json(text)
            cache_put(key, text)
            return data
        return parse_json(text)
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
