"""Watch mode with fake sources and no network."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("CONTAGION_DATA_DIR", tempfile.mkdtemp())
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient

from app import llm, store
from app.models import Item
from app.sources.base import Source
from app.watch import engine, score
from app.watch.models import Watch, WatchInput

NOW = datetime.now(timezone.utc)
iso = lambda h: (NOW - timedelta(hours=h)).isoformat(timespec="seconds")


class FakeNews(Source):
    name, platform = "google_news", "News"

    def __init__(self):
        self.round = 0

    async def search(self, plan):
        self.round += 1
        base = [
            Item(platform="News", url="https://news.example.com/acme-recall", domain="news.example.com", author="Example News",
                 title="Acme bottles recalled over lead claims", published_at=iso(3)),
            Item(platform="News", url="https://blog.example.com/acme-love", title="Why I love my Acme bottle", published_at=iso(40)),
        ]
        if self.round > 1:
            base.append(Item(platform="News", url="https://other.example.com/acme-lawsuit", author="Other Daily",
                             title="Acme faces class action lawsuit", published_at=iso(1)))
        return base


class FakeSky(Source):
    name, platform = "bluesky", "Bluesky"

    async def search(self, plan):
        return [Item(platform="Bluesky", url=f"https://bsky.app/profile/u{i}/post/{i}", author=f"@u{i}",
                     text=f"Acme bottle has lead?? toxic, never buying again #{i}", published_at=iso(i * 0.5), engagement=50)
                for i in range(1, 6)] + [Item(platform="Bluesky", url="https://bsky.app/profile/x/post/z", text="unrelated post about cats")]


class Broken(Source):
    name, platform = "hackernews", "Hacker News"

    async def search(self, plan):
        raise RuntimeError("503")


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_cycle_keyword_fallback_builds_narratives_alerts_and_playbook():
    w = Watch(input=WatchInput(brand="Acme"))
    store.save(w)
    srcs = [(FakeNews(), 1), (FakeSky(), 1), (Broken(), 1)]
    news = srcs[0][0]

    async def go():
        await engine.cycle(w, sources=srcs)
        first_alerts = len(w.alerts)
        await engine.cycle(w, sources=srcs)
        return first_alerts

    first_alerts = run(go())
    assert w.triage_mode == "keyword fallback"
    assert "error" in w.sources_status["Hacker News"]
    relevant = [m for m in w.mentions if m.relevant]
    assert len(relevant) == 8 and len(w.mentions) == 9   # cat post is irrelevant, no duplicates across cycles
    top = w.narratives[0]
    assert top.title == "Safety and health claims" and top.severity == 3
    assert set(top.platforms) == {"News", "Bluesky"}
    assert top.score >= 45 and top.status in ("escalating", "emerging")
    assert first_alerts >= 1 and any(a.level == "critical" for a in w.alerts)
    assert top.playbook is not None and top.playbook.by == "template"
    acts = [a for a in w.actions if a.narrative_id == top.id]
    assert any(a.kind == "internal_brief" for a in acts) and all(a.status == "pending" for a in acts)
    assert any("Lawsuit" in n.title for n in w.narratives)   # appeared in cycle 2
    praise = next(n for n in w.narratives if n.threat_type == "praise")
    assert praise.score < 25


def test_score_is_monotonic_in_severity():
    from app.watch.models import Narrative
    a = Narrative(title="a", severity=1, count=5, count_24h=5, platforms=["News"], negative_share=0.5)
    b = a.model_copy(update={"severity": 3})
    assert score.score(b)[0] > score.score(a)[0]
    assert score.level_for(80) == "escalate" and score.level_for(10) == "monitor"


def test_claude_triage_path(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)

    async def fake_json(system, user, max_tokens=0):
        if "brand-risk analyst" in system:
            n = user.count("\n[")
            return {"mentions": [{"i": i, "relevant": True, "sentiment": "negative", "threat_type": "rumor", "severity": 2,
                                  "is_claim": True, "stance": "spreading", "summary": "Says Acme adds surge pricing", "narrative": "new: Surge pricing rumor"}
                                 for i in range(1, n + 1)],
                    "new_narratives": [{"title": "Surge pricing rumor", "claim": "Acme will add surge pricing", "summary": "s"}]}
        return {"what": "w", "who": "x", "how_fast": "y", "why_it_matters": "z", "response_level": "respond", "level_reason": "r",
                "actions": [{"kind": "holding_statement", "title": "Approve statement", "owner": "PR lead", "timing": "today",
                             "draft": "We are not adding surge pricing. See https://evil.example.com", "target_url": "https://not-in-evidence.com"}]}

    monkeypatch.setattr(llm, "complete_json", fake_json)
    w = Watch(input=WatchInput(brand="Acme"))
    store.save(w)
    run(engine.cycle(w, sources=[(FakeSky(), 1)]))
    n = w.narratives[0]
    assert n.title == "Surge pricing rumor" and n.claim == "Acme will add surge pricing"
    assert n.playbook and n.playbook.by == "claude" and n.playbook.response_level == "respond"
    a = w.actions[0]
    assert a.target_url == "" and any("Link not in evidence" in f for f in a.flags)


def test_api_roundtrip(monkeypatch):
    from app import main
    monkeypatch.setattr(engine, "start", lambda w: None)
    c = TestClient(main.app)
    r = c.post("/api/watch", json={"brand": "Acme", "keywords": ["acme bottle", " "]})
    wid = r.json()["id"]
    w = store.get(wid)
    run(engine.cycle(w, sources=[(FakeNews(), 1), (FakeSky(), 1)]))
    d = c.get(f"/api/watch/{wid}").json()
    assert d["mode"] == "watch" and d["mentions"] and d["narratives"]
    aid = d["actions"][0]["id"]
    assert c.post(f"/api/watch/{wid}/actions/{aid}", json={"decision": "approve", "text": "Edited text"}).json()["status"] == "approved"
    csv_text = c.get(f"/api/watch/{wid}/export.csv").text
    assert "Edited text" in csv_text
    assert c.post(f"/api/watch/{wid}/control", json={"command": "pause"}).json()["status"] == "paused"
    assert any(row["mode"] == "watch" for row in c.get("/api/cases").json()["cases"])
    assert c.post(f"/api/cases/{wid}/save-replay", json={"name": "acme-watch"}).status_code == 200
