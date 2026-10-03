"""End-to-end tests with all network replaced by fakes (no keys needed)."""
import asyncio
import json
import os
import tempfile

os.environ["CONTAGION_DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ANTHROPIC_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app import analyze, llm, pipeline, spread, store
from app.models import Case, CaseInput, Item
from app.probes.engines import Engine
from app.sources.base import Source
from app.sources.gnews import parse_rss

CI = CaseInput(brand="Acme", claim="Acme will add surge pricing at lunch", truth="Acme will not use surge pricing.")


class FakeSource(Source):
    name = platform = "Reddit"

    async def search(self, plan):
        return [
            Item(platform="Reddit", url="https://www.reddit.com/r/a/1", title="Acme surge pricing is outrageous",
                 published_at="2024-02-26T12:00:00+00:00", engagement=900, outbound_links=["https://news.example.com/acme"]),
            Item(platform="News", url="https://news.example.com/acme", domain="news.example.com",
                 title="Acme to test surge pricing", published_at="2024-02-26T08:00:00+00:00"),
            Item(platform="News", url="https://fact.example.org/acme", domain="fact.example.org",
                 title="Acme denies surge pricing, clarifies plans", published_at="2024-02-28T09:00:00+00:00"),
        ]


class BrokenSource(Source):
    name = platform = "Bluesky"

    async def search(self, plan):
        raise RuntimeError("403 forbidden")


class FakeEngine(Engine):
    name, mode = "FakeGPT", "live web"

    async def ask(self, q):
        return "Yes, Acme is adding surge pricing.", [{"url": "https://news.example.com/acme", "title": "Acme"}]


def fake_llm(monkeypatch):
    """Deterministic stand-in for Claude, keyed on the system prompt."""
    monkeypatch.setattr(llm, "available", lambda: True)

    async def complete_json(system, user, max_tokens=0):
        if "plan research" in system:
            return {"queries": ["acme surge pricing"], "questions": ["Does Acme use surge pricing?"]}
        if "label public posts" in system:
            n = user.count("\n[")
            out = []
            for i in range(n):
                line = user.split(f"[{i}]")[1].split("\n")[0].lower()
                out.append({"i": i, "stance": "debunks" if "denies" in line else "amplifies", "reason": "test"})
            return out
        if "skeptical second reviewer. Another analyst" in system:
            return [{"i": i, "confirmed": True} for i in range(user.count("\n["))]
        if "audit an AI assistant" in system:
            return {"verdict": "repeats", "reason": "says yes", "quote": "Yes"}
        if "A first reviewer said this AI answer" in system:
            return {"confirmed": True, "reason": "states it as fact"}
        return {}

    async def complete(system, user, **kw):
        return "FACT CHECK: Acme will not use surge pricing.", []

    monkeypatch.setattr(llm, "complete_json", complete_json)
    monkeypatch.setattr(llm, "complete", complete)


def patch_world(monkeypatch):
    monkeypatch.setattr(pipeline, "all_sources", lambda: [FakeSource(), BrokenSource()])
    monkeypatch.setattr(pipeline, "all_engines", lambda: [FakeEngine()])


def test_full_run_with_model(monkeypatch):
    patch_world(monkeypatch)
    fake_llm(monkeypatch)
    case = Case(input=CI)
    asyncio.run(pipeline.run_case(case))

    assert case.status == "done", [l.message for l in case.log]
    assert case.sources_status["Bluesky"].startswith("error"), "broken source is reported, not fatal"
    s = case.spread
    assert s["amplifiers"] == 2 and s["debunkers"] == 1
    first = next(i for i in case.items if i.id == s["earliest_amplifier_id"])
    assert first.platform == "News"                   # 08:00 news beat the 12:00 Reddit post
    assert s["correction_lag_hours"] == 49.0
    assert s["edges"], "Reddit post linking to the news article becomes an edge"
    assert case.grade["level"] == 4                   # confirmed AI repeat
    kinds = [a.kind for a in case.actions]
    assert {"fact_sheet", "site_fix", "correction_request", "platform_flag"} <= set(kinds)
    assert all(a.status == "pending" for a in case.actions), "nothing is pre-approved"


def test_unconfirmed_repeat_does_not_escalate(monkeypatch):
    patch_world(monkeypatch)
    fake_llm(monkeypatch)
    orig = llm.complete_json

    async def deny_second_review(system, user, max_tokens=0):
        if "A first reviewer said this AI answer" in system:
            raise RuntimeError("timeout")
        return await orig(system, user, max_tokens)

    monkeypatch.setattr(llm, "complete_json", deny_second_review)
    case = Case(input=CI)
    asyncio.run(pipeline.run_case(case))
    assert case.spread["contamination"]["repeats"] == 0
    assert case.spread["contamination"]["repeats_unconfirmed"] == 1
    assert case.grade["level"] == 3


def test_skeptic_failure_blocks_platform_flags(monkeypatch):
    patch_world(monkeypatch)
    fake_llm(monkeypatch)
    orig = llm.complete_json

    async def broken_skeptic(system, user, max_tokens=0):
        if "Another analyst" in system:
            raise RuntimeError("bad json")
        return await orig(system, user, max_tokens)

    monkeypatch.setattr(llm, "complete_json", broken_skeptic)
    case = Case(input=CI)
    asyncio.run(pipeline.run_case(case))
    assert not [a for a in case.actions if a.kind == "platform_flag"]


def test_runs_without_any_model(monkeypatch):
    patch_world(monkeypatch)
    case = Case(input=CI)
    asyncio.run(pipeline.run_case(case))
    assert case.status == "done"
    assert case.actions and case.actions[0].kind == "fact_sheet"


def test_debunk_citation_is_not_contamination():
    items = [Item(platform="News", url="https://news.google.com/x", domain="cnn.com", stance="debunks")]
    from app.models import Probe
    p = Probe(engine="E", question="q", verdict="repeats", confirmed=True, citations=[{"url": "https://cnn.com/other"}])
    c = spread.cross_reference([p], items)
    assert c["traced_pages_cited"] == 0


def test_guardrail_flags_new_numbers():
    from app.respond import guardrail
    flags = guardrail("Prices rise 20% at noon", CI, [])
    assert any("20%" in f for f in flags)


def test_guardrail_ignores_digits_in_links():
    from app.respond import guardrail
    assert guardrail("Post: https://www.reddit.com/r/a/123", CI, ["https://www.reddit.com/r/a/123"]) == []


def test_rss_parse():
    xml = """<rss><channel><item><title>Acme denies it - Daily News</title><link>https://news.google.com/r/1</link>
    <pubDate>Wed, 28 Feb 2024 10:00:00 GMT</pubDate><source url="https://dailynews.com">Daily News</source></item></channel></rss>"""
    items = parse_rss(xml)
    assert items[0].title == "Acme denies it" and items[0].domain == "dailynews.com"
    assert items[0].published_at.startswith("2024-02-28")


def test_api_and_approval_gate(monkeypatch):
    patch_world(monkeypatch)
    fake_llm(monkeypatch)
    case = Case(input=CI)
    asyncio.run(pipeline.run_case(case))
    store.save(case)

    from app.main import app
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"]
    assert client.get("/").status_code == 200
    body = client.get(f"/api/cases/{case.id}").json()
    assert body["status"] == "done"

    # Nothing approved -> export is header only.
    csv_text = client.get(f"/api/cases/{case.id}/export.csv").text
    assert csv_text.strip().count("\n") == 0

    act = body["actions"][0]
    r = client.post(f"/api/cases/{case.id}/actions/{act['id']}", json={"decision": "approve", "text": "Edited text"})
    assert r.json()["status"] == "approved" and r.json()["final_text"] == "Edited text"
    assert "Edited text" in client.get(f"/api/cases/{case.id}/export.csv").text

    assert client.post(f"/api/cases/{case.id}/actions/{act['id']}", json={"decision": "nope"}).status_code == 400

    # Replay round trip.
    name = client.post(f"/api/cases/{case.id}/save-replay", json={"name": "Acme Demo"}).json()["name"]
    new_id = client.post(f"/api/replays/{name}").json()["id"]
    replay = client.get(f"/api/cases/{new_id}").json()
    assert replay["replay"] is True and new_id != case.id


def test_token_gate(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "ACCESS_TOKEN", "s3cret")
    client = TestClient(m.app)
    assert client.get("/api/cases").status_code == 401
    assert client.get("/api/cases", headers={"X-Access-Token": "s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200
