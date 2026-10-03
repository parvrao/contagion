"""CPG mode: search terms, threat types, health claims never silenced, FDA recall lookup."""
import asyncio

from app import http
from app.watch import engine, judgment, recalls, triage
from app.watch.models import Mention, Narrative, Watch, WatchInput


def test_cpg_adds_search_terms_only_when_selected():
    plain = engine.queries(Watch(input=WatchInput(brand="Acme Snacks")))
    cpg = engine.queries(Watch(input=WatchInput(brand="Acme Snacks", industry="cpg", keywords=["acme chips"])))
    assert not any("shrinkflation" in q for q in plain)
    assert any("shrinkflation" in q for q in cpg) and "acme chips" in cpg


def test_keyword_fallback_knows_cpg_types():
    w = Watch(input=WatchInput(brand="Acme"))
    cases = {"Acme chips still use red 40 dye": "ingredient", "Acme changed the recipe and it tastes different": "formula_change",
             "Acme cereal discontinued??": "availability", "Acme cocoa linked to child labor": "sourcing"}
    for text, want in cases.items():
        m = Mention(platform="Bluesky", url=f"https://x/{want}", text=text)
        triage._heuristic(w, m)
        assert m.threat_type == want, (text, m.threat_type)


def test_ingredient_health_claim_is_never_silenced():
    n = Narrative(title="Banned dye", claim="Acme uses a dye banned in Europe", threat_type="ingredient", severity=2,
                  platforms=["TikTok"], engagement=200)
    assert judgment.amplification(n, brand_audience=5_000_000, veracity="unclear")["verdict"] == "act"


def test_recall_check_only_for_cpg_safety_or_recall_claims():
    assert recalls.applies("cpg", "safety", "")
    assert recalls.applies("cpg", "rumor", "Acme recalled its chips")
    assert not recalls.applies("", "safety", "")
    assert not recalls.applies("cpg", "pricing", "price went up")


def test_recall_check_parses_records(monkeypatch):
    async def fake(url, **kw):
        assert "recalling_firm" in url
        return {"meta": {"results": {"total": 1}}, "results": [{"report_date": "20260912", "recalling_firm": "Acme Foods",
                "product_description": "Acme Chips 8oz", "reason_for_recall": "Undeclared milk", "classification": "Class II",
                "status": "Ongoing", "recall_number": "F-1234-2026"}]}
    monkeypatch.setattr(http, "get_json", fake)
    rc = asyncio.run(recalls.check("Acme"))
    assert rc["total"] == 1 and rc["items"][0]["date"] == "2026-09-12" and "Undeclared milk" in recalls.prompt_line(rc)


def test_no_recall_record_is_not_called_false(monkeypatch):
    async def fake(url, **kw):
        raise http.UpstreamError("api.fda.gov returned 404: NOT_FOUND", 404)
    monkeypatch.setattr(http, "get_json", fake)
    rc = asyncio.run(recalls.check("Acme"))
    assert rc["checked"] and not rc["items"] and not rc["error"]
    assert "not proof" in recalls.prompt_line(rc)
