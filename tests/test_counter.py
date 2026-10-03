"""Counter mode tests with fakes (no keys, no network)."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CONTAGION_DATA_DIR", tempfile.mkdtemp())

import pytest
from fastapi.testclient import TestClient

from app import llm, store
from app.counter import engine, inventory
from app.counter.models import CounterCase, CounterInput
from app.models import Item
from app.sources.base import Source

SAMPLE = open("data/samples/inventory_sample.csv").read()
NOW = datetime.now(timezone.utc)


def ci(**kw):
    base = dict(our_brand="Northpace", competitor="Zephyr", competitor_product="AquaDash", category="waterproof running shoes",
                inventory_source="csv", csv_text=SAMPLE)
    base.update(kw)
    return CounterInput(**base)


def test_rules_on_sample():
    skus = inventory.load_csv(SAMPLE)
    inventory.apply_rules(skus, ci())
    by = {s.sku: s for s in skus}
    assert by["NP-STORM-01"].flagged and by["NP-STORM-01"].days_of_inventory == 160.4
    assert by["NP-STORM-01"].max_cac == 41.5          # (130-47) * 50%
    assert not by["NP-DAILY-03"].flagged               # fast mover: 16 days of supply
    assert not by["NP-RACE-04"].flagged                # margin 20% < 30%
    assert by["NP-WALK-05"].flagged


def test_csv_errors_are_clear():
    with pytest.raises(inventory.InventoryError, match="missing columns"):
        inventory.load_csv("a,b\n1,2")


class Complaints(Source):
    name = platform = "Reddit"

    async def search(self, plan):
        rows = []
        for i, plat in enumerate(["Reddit", "Reddit", "YouTube", "Bluesky", "News & web"]):
            rows.append(Item(platform=plat, url=f"https://ex.com/{i}", title=f"My AquaDash leaked on first run #{i}",
                             published_at=(NOW - timedelta(days=2)).isoformat(), engagement=100))
        rows.append(Item(platform="Reddit", url="https://ex.com/r", title="Heard AquaDash soles fall off, people are saying",
                         published_at=NOW.isoformat()))
        return rows


def fake_llm(monkeypatch, evidence="seam-sealed membrane upper"):
    monkeypatch.setattr(llm, "available", lambda: True)

    async def cj(system, user, max_tokens=0):
        if "plan social listening" in system:
            return {"queries": ["aquadash leaking"]}
        if "label public posts about a product" in system:
            out = []
            for i in range(user.count("\n[")):
                line = user.split(f"[{i}]")[1].split("\n")[0]
                rumor = "people are saying" in line
                out.append({"i": i, "complaint": True, "friction": "durability" if rumor else "quality defect",
                            "first_hand": not rumor, "rumor": rumor, "quote": "leaked on first run"})
            return out
        if "match a competitor" in system:
            cid = user.split("[")[1].split("]")[0]
            return [{"cluster_id": cid, "sku": "NP-STORM-01", "fit": 0.9, "evidence": evidence, "angle": "dry feet"},
                    {"cluster_id": cid, "sku": "NP-WALK-05", "fit": 0.5, "evidence": "fully waterproof", "angle": "fake"}]
        if "paid social ad drafts" in system:
            return {"hooks": ["Wet socks again?"], "headlines": ["Stay dry, mile 1 to mile 20", "Sealed seams"],
                    "body": "Seam-sealed and backed by a lifetime seal warranty. Unlike AquaDash.",
                    "cta": "Shop Now", "audience_ideas": ["trail running", "rainy-city runners"]}
        return {}

    monkeypatch.setattr(llm, "complete_json", cj)


def run(monkeypatch, **kw):
    monkeypatch.setattr(engine, "all_sources", lambda: [Complaints()])
    case = CounterCase(input=ci(**kw))
    asyncio.run(engine.run_counter(case))
    return case


def test_full_counter_run(monkeypatch):
    fake_llm(monkeypatch)
    case = run(monkeypatch)
    assert case.status == "done", [l.message for l in case.log]
    by = {c.friction: c for c in case.clusters}
    assert by["quality defect"].reality == "verified"
    assert by["durability"].reality == "disputed"          # rumor cluster is blocked
    assert [m.sku for m in case.matches] == ["NP-STORM-01"], "evidence not in the description is dropped (WALK)"
    ad = case.actions[0]
    assert ad.kind == "ad_package" and ad.status == "pending"
    assert any("AquaDash" in f for f in ad.guardrail_flags), "competitor name in copy is flagged"
    assert any("'20'" in f for f in ad.guardrail_flags), "invented number is flagged"
    assert ad.meta["max_cac"] == 41.5


def test_no_verified_cluster_no_ads(monkeypatch):
    fake_llm(monkeypatch)

    class Thin(Source):
        name = platform = "Reddit"

        async def search(self, plan):
            return [Item(platform="Reddit", url="https://ex.com/1", title="my AquaDash leaked", published_at=NOW.isoformat())]

    monkeypatch.setattr(engine, "all_sources", lambda: [Thin()])
    case = CounterCase(input=ci())
    asyncio.run(engine.run_counter(case))
    assert case.status == "done" and not case.actions


def test_shopify_not_configured_fails_clearly(monkeypatch):
    for k in ("SHOPIFY_STORE", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    case = CounterCase(input=ci(inventory_source="shopify"))
    asyncio.run(engine.run_counter(case))
    assert case.status == "failed" and "Shopify not configured" in case.log[-1].message


def test_counter_api_and_paused_export(monkeypatch):
    fake_llm(monkeypatch)
    case = run(monkeypatch)
    store.save(case)
    from app.main import app
    c = TestClient(app)
    assert c.get(f"/api/cases/{case.id}").json()["mode"] == "counter"
    assert "NP-STORM-01" in c.get("/api/samples/inventory.csv").text
    a = case.actions[0]
    clean = a.draft.replace(" Unlike AquaDash.", "")
    assert c.post(f"/api/cases/{case.id}/actions/{a.id}", json={"decision": "approve", "text": clean}).status_code == 200
    out = c.get(f"/api/cases/{case.id}/export.csv").text
    assert "PAUSED" in out and "Stay dry" in out
    assert c.post(f"/api/cases/{case.id}/recheck").status_code == 409
    assert any(r["mode"] == "counter" for r in c.get("/api/cases").json()["cases"])
    assert c.post("/api/counter", json={"our_brand": "x", "competitor": "y", "inventory_source": "csv"}).status_code == 400


def test_trademark_blocks_approval_until_edited(monkeypatch):
    fake_llm(monkeypatch)
    case = run(monkeypatch)
    store.save(case)
    from app.main import app
    c = TestClient(app)
    a = case.actions[0]
    r = c.post(f"/api/cases/{case.id}/actions/{a.id}", json={"decision": "approve", "text": a.draft})
    assert r.status_code == 409
    clean = a.draft.replace(" Unlike AquaDash.", "").replace("mile 1 to mile 20", "every mile")
    r = c.post(f"/api/cases/{case.id}/actions/{a.id}", json={"decision": "approve", "text": clean})
    assert r.status_code == 200 and r.json()["guardrail_flags"] == []


def test_number_guard_uses_whole_numbers():
    from app.respond import numbers_in
    assert "1" not in numbers_in("price 130")
    assert "130" in numbers_in("price 130.00")


def test_dollar_impact_and_stockout_guard():
    skus = inventory.load_csv(SAMPLE)
    inp = ci(stockout_days=20)
    inventory.apply_rules(skus, inp)
    by = {s.sku: s for s in skus}
    storm = by["NP-STORM-01"]
    assert storm.cash_tied == storm.surplus_units * 47
    assert storm.holding_cost_month == round(storm.cash_tied * 0.25 / 12, 2)
    assert by["NP-DAILY-03"].stockout_risk and "do not advertise" in by["NP-DAILY-03"].flag_reason
    imp = inventory.impact(skus, inp)
    assert imp["stockout_guard"][0]["sku"] == "NP-DAILY-03"
    assert imp["cash_tied"] == round(sum(s.cash_tied for s in skus if s.flagged), 2)


def test_spike_uses_window_end_for_historical_runs(monkeypatch):
    fake_llm(monkeypatch)
    until = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    case = run(monkeypatch, until=until)
    qd = next(c for c in case.clusters if c.friction == "quality defect")
    assert qd.recent_7d == 5 and qd.baseline_week == 0 and qd.new_signal and not qd.spiking
    assert qd.engagement_spike is None


def test_competitor_facts_freshness_and_guard(monkeypatch):
    from app.counter.models import CompetitorFact
    today = NOW.strftime("%Y-%m-%d")
    fresh = CompetitorFact(fact="list price", value="$180", source_url="https://shop.example.com/aquadash", checked_on=today)
    stale = CompetitorFact(fact="weight", value="310 g", source_url="https://x.example.com", checked_on="2025-01-01")
    inp = ci(competitor_facts=[fresh, stale])
    ok, problems = engine.usable_facts(inp)
    assert [f["value"] for f in ok] == ["$180"] and "re-check" in problems[0]
    skus = inventory.load_csv(SAMPLE)
    storm = next(s for s in skus if s.sku == "NP-STORM-01")
    assert engine.guard_ad("Paying $180 for wet socks? $130 and sealed.", inp, storm) == []
    assert engine.guard_ad("Paying $200 for wet socks?", inp, storm)


def test_brief_is_attached_and_sent(monkeypatch):
    fake_llm(monkeypatch)
    seen = {}
    orig = llm.complete_json

    async def spy(system, user, max_tokens=0):
        if "paid social ad drafts" in system:
            seen["user"] = user
        return await orig(system, user, max_tokens)

    monkeypatch.setattr(llm, "complete_json", spy)
    case = run(monkeypatch)
    brief = case.actions[0].meta["brief"]
    assert brief["candidate_sku"]["verbatim_strength"] == "seam-sealed membrane upper"
    assert brief["competitor_weakness"]["first_hand"] == 5
    assert '"verbatim_strength": "seam-sealed membrane upper"' in seen["user"]
    assert case.summary["impact"]["cash_tied"] > 0


def test_spike_against_real_baseline(monkeypatch):
    fake_llm(monkeypatch)

    class WithBaseline(Source):
        name = platform = "Reddit"

        async def search(self, plan):
            rows = [Item(platform=p, url=f"https://ex.com/n{i}", title=f"my AquaDash leaked #{i}",
                         published_at=(NOW - timedelta(days=1)).isoformat(), engagement=50)
                    for i, p in enumerate(["Reddit", "YouTube", "Bluesky", "Reddit", "Reddit", "YouTube", "Reddit", "Bluesky"])]
            rows += [Item(platform="Reddit", url=f"https://ex.com/o{i}", title=f"my AquaDash leaked old #{i}",
                          published_at=(NOW - timedelta(days=20)).isoformat(), engagement=10) for i in range(4)]
            return rows

    monkeypatch.setattr(engine, "all_sources", lambda: [WithBaseline()])
    case = CounterCase(input=ci())
    asyncio.run(engine.run_counter(case))
    qd = next(c for c in case.clusters if c.friction == "quality defect")
    assert qd.baseline_week == 1.0 and qd.recent_7d == 8
    assert qd.spike == 4.5 and qd.spiking and not qd.new_signal
    assert qd.engagement_spike == 40.0     # 400 this week vs 10/week baseline
