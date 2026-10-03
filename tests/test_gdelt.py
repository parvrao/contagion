import asyncio

import pytest

from app import http
from app.sources import gdelt
from app.sources.base import SearchPlan


class Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_gate_spaces_calls_caches_and_survives_429(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(gdelt, "MIN_GAP", 0.01)
    monkeypatch.setattr(gdelt, "BACKOFF_429", 0.0)
    gdelt._cache.clear()

    async def fake_request(method, url, params=None, **kw):
        calls.append(params["query"])
        if params["query"].startswith("bad"):
            raise http.UpstreamError("429", 429)
        return Resp({"articles": [{"url": f"https://n.example.com/{len(calls)}", "title": "t", "seendate": "20261003T120000Z", "domain": "n.example.com"}]})

    monkeypatch.setattr(gdelt.http, "request", fake_request)
    src = gdelt.GdeltSource()
    plan = SearchPlan(brand="Acme", claim="x", queries=["acme a", "bad b"])
    items = asyncio.run(src.search(plan))
    assert len(items) == 1 and calls.count("bad b sourcelang:english") == 2   # one retry, then skip
    asyncio.run(src.search(SearchPlan(brand="Acme", claim="x", queries=["acme a"])))
    assert calls.count("acme a sourcelang:english") == 1                       # second call served from cache
    with pytest.raises(RuntimeError, match="rate-limiting"):
        asyncio.run(src.search(SearchPlan(brand="Acme", claim="x", queries=["bad only"])))
