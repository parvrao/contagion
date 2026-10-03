"""The always-on loop behind a Watch.

Each cycle: poll due sources > keep only new URLs > triage > recompute
narratives > raise alerts > draft playbooks for serious narratives.
Sources fail soft. Slow or costly sources run on a longer cadence.
"""
from __future__ import annotations

import asyncio
import json
import os
import traceback
from datetime import datetime, timedelta, timezone

from .. import http, store
from ..models import LogLine, now_iso
from ..sources.base import SearchPlan
from ..sources.bluesky import BlueskySource
from ..sources.gnews import GoogleNewsSource
from ..sources.hackernews import HackerNewsSource
from ..sources.reddit import RedditSource
from ..sources.websearch import WebSearchSource
from ..sources.youtube import YouTubeSource
from . import ai, playbook, score, triage
from .models import Alert, Mention, Watch

POLL_SECONDS = int(os.environ.get("CONTAGION_POLL_SECONDS", "90"))
MAX_MENTIONS = 1500
AUTO_PLAYBOOK_SCORE = int(os.environ.get("CONTAGION_AUTO_PLAYBOOK_SCORE", "45"))
AI_EVERY = int(os.environ.get("CONTAGION_AI_EVERY", "7"))   # Profound check every N cycles (~10 min)

# (source, run every N cycles)
CADENCE = [
    (GoogleNewsSource(), 1),
    (HackerNewsSource(), 1),
    (BlueskySource(), 1),
    (RedditSource(), 5),
    (YouTubeSource(), int(os.environ.get("CONTAGION_YOUTUBE_EVERY", "10"))),   # 100 quota units per search
    (WebSearchSource(), int(os.environ.get("CONTAGION_SWEEP_EVERY", "10"))),   # Claude web search, costs credits
]

_tasks: dict[str, asyncio.Task] = {}
_wake: dict[str, asyncio.Event] = {}
_locks: dict[str, asyncio.Lock] = {}


def _log(w: Watch, stage: str, message: str, level: str = "info") -> None:
    line = LogLine(stage=stage, message=message, level=level)
    w.log.append(line)
    w.log = w.log[-300:]
    store.publish(w.id, {"type": "log", "line": line.model_dump()})


def norm(url: str) -> str:
    url = url.split("#")[0].rstrip("/")
    if "utm_" in url:
        base, _, q = url.partition("?")
        keep = "&".join(p for p in q.split("&") if not p.startswith("utm_"))
        url = base + ("?" + keep if keep else "")
    return url


def queries(w: Watch) -> list[str]:
    b = w.input.brand.strip()
    qs = [f'"{b}"' if " " in b else b]
    if w.input.product:
        qs.append(f"{b} {w.input.product}".strip())
    qs += [k.strip() for k in w.input.keywords if k.strip()]
    return qs[:5]


def start(w: Watch) -> None:
    if w.id in _tasks and not _tasks[w.id].done():
        return
    _wake[w.id] = asyncio.Event()
    _locks.setdefault(w.id, asyncio.Lock())
    _tasks[w.id] = asyncio.create_task(_loop(w.id))


def poke(watch_id: str) -> None:
    if watch_id in _wake:
        _wake[watch_id].set()


def is_running(watch_id: str) -> bool:
    return watch_id in _tasks and not _tasks[watch_id].done()


async def _loop(watch_id: str) -> None:
    while True:
        w = store.get(watch_id)
        if w is None or w.status in ("stopped", "failed"):
            return
        if w.status == "paused":
            await _sleep(watch_id, 5)
            continue
        try:
            await cycle(w)
        except Exception as exc:  # noqa: BLE001  (log and keep watching)
            _log(w, "error", f"Cycle failed: {exc!r}", "error")
            traceback.print_exc()
        w.next_poll_at = (datetime.now(timezone.utc) + timedelta(seconds=w.poll_seconds)).isoformat(timespec="seconds")
        w.stage = "waiting"
        store.save(w)
        store.publish(w.id, {"type": "update", "stage": "waiting", "next_poll_at": w.next_poll_at})
        await _sleep(watch_id, w.poll_seconds)


async def _sleep(watch_id: str, seconds: float) -> None:
    ev = _wake.get(watch_id)
    if ev is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(ev.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    ev.clear()


async def cycle(w: Watch, sources=None) -> None:
    async with _locks.setdefault(w.id, asyncio.Lock()):
        first = w.cycles == 0
        w.cycles += 1
        w.status = "running"
        w.stage = "polling"
        store.publish(w.id, {"type": "update", "stage": "polling"})
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=7 if first else 2)).strftime("%Y-%m-%d")
        plan = SearchPlan(brand=w.input.brand, claim=f"latest public discussion, complaints, rumors or controversies about {w.input.brand}",
                          queries=queries(w), since=since, limit=40, extra={"sort": "latest"})

        due = []
        for src, every in (sources or CADENCE):
            ok, note = src.enabled()
            if not ok:
                w.sources_status[src.platform if src.name != "web" else "Open web (Claude search)"] = f"skipped: {note}"
                continue
            if first or (w.cycles - 1) % every == 0:
                due.append(src)

        slow = {"web", "youtube"}
        fast_due = [s for s in due if s.name not in slow]
        slow_due = [s for s in due if s.name in slow]
        total_new = 0
        for group in (fast_due, slow_due):   # fast sources show up in seconds; search sweeps follow
            if group:
                total_new += await _poll_group(w, group, plan, first, now)
        w.last_poll_at = now_iso()
        if not total_new:
            _log(w, "poll", f"Cycle {w.cycles}: no new mentions from {len(due)} sources.")
        if first or (w.cycles - 1) % AI_EVERY == 0:
            await check_ai(w)


async def check_ai(w: Watch) -> None:
    """Profound pass: which threat narratives are already in AI answers."""
    w.stage = "ai_check"
    store.publish(w.id, {"type": "update", "stage": "ai_check"})
    before = score.snapshot(w)
    try:
        await ai.refresh(w)
    except Exception as exc:  # noqa: BLE001
        w.ai_status = f"error: {str(exc)[:160]}"
    w.ai_checked_at = now_iso()
    _log(w, "ai", f"Profound: {w.ai_status}", "info" if w.ai_status.startswith("ok") else "warn")
    score.recompute(w)
    fresh = [a for a in score.alerts_for(w, before, False) if a.title.startswith("In AI answers")]
    for a in fresh:
        w.alerts.insert(0, a)
        store.publish(w.id, {"type": "alert", "alert": a.model_dump()})
    if fresh:
        asyncio.create_task(deliver(w, fresh))
    store.save(w)


async def _poll_group(w: Watch, group, plan, first: bool, now) -> int:
    results = await asyncio.gather(*[asyncio.wait_for(s.search(plan), timeout=150) for s in group], return_exceptions=True)
    seen = {norm(m.url) for m in w.mentions}
    new: list[Mention] = []
    for src, res in zip(group, results):
        label = src.platform if src.name != "web" else "Open web (Claude search)"
        w.source_last_run[label] = now_iso()
        if isinstance(res, Exception):
            w.sources_status[label] = f"error: {str(res)[:160] or type(res).__name__}"
            _log(w, "poll", f"{label}: {str(res)[:160] or type(res).__name__}", "warn")
            continue
        added = 0
        for it in res:
            key = norm(it.url)
            if not key or key in seen:
                continue
            seen.add(key)
            new.append(Mention(platform=it.platform, url=it.url, domain=it.domain, title=it.title, text=it.text,
                               author=it.author, published_at=it.published_at, engagement=it.engagement, source=src.name))
            added += 1
        w.sources_status[label] = f"ok ({added} new)"
    if not new:
        return 0

    new.sort(key=lambda m: m.published_at or "", reverse=True)
    w.mentions = (new + w.mentions)[:MAX_MENTIONS]
    _log(w, "poll", f"Cycle {w.cycles}: {len(new)} new mentions from {', '.join(s.platform for s in group)}.")
    store.save(w)
    store.publish(w.id, {"type": "mentions", "count": len(new)})

    w.stage = "triage"
    store.publish(w.id, {"type": "update", "stage": "triage"})
    before = score.snapshot(w)

    def progress():
        score.recompute(w)
        store.save(w)
        store.publish(w.id, {"type": "update", "stage": "triage"})

    created = await triage.triage(w, new, on_batch=progress)
    score.recompute(w)
    relevant = sum(m.relevant for m in new)
    _log(w, "triage", f"Triaged {len(new)} ({relevant} relevant) with {w.triage_mode}; {len(created)} new narratives.")

    fresh = score.alerts_for(w, before, first)
    for a in fresh:
        w.alerts.insert(0, a)
    w.alerts = w.alerts[:200]
    store.save(w)
    for a in fresh:
        store.publish(w.id, {"type": "alert", "alert": a.model_dump()})
    if fresh:
        asyncio.create_task(deliver(w, fresh))

    # Draft playbooks for serious narratives that have none yet (max 2 per pass to control cost).
    todo = [n for n in w.narratives if n.playbook is None and n.score >= AUTO_PLAYBOOK_SCORE][:2]
    for n in todo:
        w.stage = "playbook"
        store.publish(w.id, {"type": "update", "stage": "playbook"})
        await make_playbook(w, n)
    return len(new)


async def make_playbook(w, n) -> None:
    pb, acts = await playbook.build(w, n)
    n.playbook = pb
    w.actions = [a for a in w.actions if a.narrative_id != n.id or a.status != "pending"] + acts
    _log(w, "playbook", f"Playbook for \"{n.title}\": {pb.response_level}, {len(acts)} actions ({pb.by}).")
    store.save(w)
    store.publish(w.id, {"type": "update", "stage": w.stage})


def dashboard_url(w: Watch) -> str:
    base = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    return f"{base}/#/watch/{w.id}" if base else ""


async def deliver(w: Watch, alerts: list[Alert]) -> None:
    """Internal team alert only. The playbook still needs a person to approve anything external."""
    hook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not hook:
        return
    link = dashboard_url(w)
    icon = {"critical": ":red_circle:", "warn": ":large_orange_circle:", "info": ":white_circle:"}
    lines = [f"*Contagion watch: {w.input.brand}*"] + [f"{icon.get(a.level, '')} *{a.title}*  {a.reason}" for a in alerts]
    if link:
        lines.append(f"<{link}|Open dashboard> (nothing is sent until a person approves)")
    try:
        await http.post_json(hook, {"text": "\n".join(lines)}, attempts=2)
        for a in alerts:
            a.delivered.append("slack")
        store.save(w)
    except Exception as exc:  # noqa: BLE001
        _log(w, "alert", f"Slack delivery failed: {str(exc)[:140]}", "warn")


def resume_all() -> None:
    """Restart watches that were running before a restart (Render free restarts often)."""
    for p in store.CASES_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("mode") == "watch" and d.get("status") in ("running", "starting") and not d.get("replay"):
            w = store.get(d["id"])
            if w:
                start(w)
