"""Caching, fast-model routing and Watch-seeded deep traces."""
import asyncio
import os
import tempfile

os.environ.setdefault("CONTAGION_DATA_DIR", tempfile.mkdtemp())

from app import http, llm, pipeline
from app.config import settings
from app.models import Case, CaseInput, Item


def test_llm_cache_and_fast_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(settings.__class__, "anthropic_api_key", property(lambda self: "k"), raising=False)
    monkeypatch.setattr(llm, "provider", lambda: "anthropic")
    monkeypatch.setattr(llm, "available", lambda: True)
    llm._mem.clear()
    llm._fast_broken["v"] = False

    async def fake_post(url, payload, **kw):
        calls.append(payload["model"])
        if payload["model"] == settings.fast_model:
            raise http.UpstreamError("model not found", 404)
        return {"content": [{"type": "text", "text": "hello"}]}

    monkeypatch.setattr(llm.http, "post_json", fake_post)

    async def go():
        with llm.fast():
            a = await llm.complete("sys-unique-1", "u")
        b = await llm.complete("sys-unique-1", "u")          # main model: separate cache key, real call
        with llm.fast():
            c = await llm.complete("sys-unique-1", "u")       # fast is now marked broken -> main model key -> cached
        with llm.no_cache():
            d = await llm.complete("sys-unique-1", "u")       # forced fresh
        return a, b, c, d

    a, b, c, d = asyncio.run(go())
    assert a[0] == b[0] == c[0] == d[0] == "hello"
    # a: fast model rejected -> retried on main; b: main model, fresh; c: cached; d: no_cache -> fresh
    assert calls == [settings.fast_model, settings.anthropic_model, settings.anthropic_model, settings.anthropic_model]


def test_deep_trace_reuses_watch_mentions(monkeypatch):
    called = []

    class Boom:
        name = platform = "Reddit"

        def enabled(self):
            return True, ""

        async def search(self, plan):
            called.append(1)
            return []

    monkeypatch.setattr(pipeline, "all_sources", lambda: [Boom()])
    monkeypatch.setattr(pipeline, "all_engines", lambda: [])
    monkeypatch.setattr(llm, "available", lambda: False)
    seeds = [Item(platform="Bluesky", url=f"https://bsky.app/p/{i}", title=f"Acme surge pricing {i}") for i in range(6)]
    case = Case(input=CaseInput(brand="Acme", claim="Acme adds surge pricing", truth="No surge pricing."))
    asyncio.run(pipeline.run_case(case, seed_items=seeds))
    assert case.status == "done" and not called and len(case.items) == 6
    assert case.sources_status["Watch mentions"] == "reused (6)"
