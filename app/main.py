"""HTTP API + static UI.

Run locally:  uvicorn app.main:app --reload
Deploy:       any host that runs a Python web process (see render.yaml / Procfile).
Set CONTAGION_ACCESS_TOKEN before exposing it publicly, or anyone with the URL
can spend your API credits.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import http, pipeline, store
from . import respond as respond_mod
from .config import ROOT, settings
from .models import Case, CaseInput, LogLine, now_iso, short_id
from .presets import PRESETS
from .probes import all_engines, profound
from .sources import all_sources
from .counter import engine as counter_engine
from .counter import inventory as counter_inventory
from .counter.models import CounterCase, CounterInput
from .watch import engine as watch_engine
from .watch.models import Watch, WatchInput

ACCESS_TOKEN = os.environ.get("CONTAGION_ACCESS_TOKEN", "").strip()
WEB_DIR = ROOT / "web"
_running: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    watch_engine.resume_all()
    yield
    await http.aclose()


app = FastAPI(title="Contagion", version="0.1.0", lifespan=lifespan)


def require_token(request: Request) -> None:
    """Optional shared-token gate. Header `X-Access-Token` or `?token=` (for EventSource)."""
    if not ACCESS_TOKEN:
        return
    given = request.headers.get("x-access-token") or request.query_params.get("token") or ""
    if not secrets.compare_digest(given, ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Access token required")


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)


def _case_or_404(case_id: str) -> Case:
    case = store.get(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


# ---------- meta ----------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config", dependencies=[Depends(require_token)])
def config():
    sources = [{"name": s.platform, "enabled": s.enabled()[0], "note": s.enabled()[1]} for s in all_sources()]
    engines = [{"name": f"{e.name} ({e.mode})", "enabled": e.enabled()[0], "note": e.enabled()[1]} for e in all_engines()]
    ok, note = profound.enabled()
    return {
        "model": settings.anthropic_model,
        "sources": sources,
        "engines": engines,
        "profound": {"enabled": ok, "note": note},
        "shopify": dict(zip(("enabled", "note"), counter_inventory.shopify_configured())),
        "csv_fields": counter_inventory.CSV_FIELDS,
        "presets": PRESETS,
        "auth": bool(ACCESS_TOKEN),
    }


@app.get("/api/auth-check")
def auth_check():
    return {"required": bool(ACCESS_TOKEN)}


# ---------- cases ----------

@app.get("/api/cases", dependencies=[Depends(require_token)])
def list_cases():
    return {"cases": store.list_cases(), "replays": store.list_replays()}


@app.post("/api/cases", dependencies=[Depends(require_token)])
async def create_case(ci: CaseInput):
    case = Case(input=ci)
    store.save(case)
    _spawn(pipeline.run_case(case))
    return {"id": case.id}


@app.post("/api/counter", dependencies=[Depends(require_token)])
async def create_counter(ci: CounterInput):
    if ci.inventory_source == "csv" and not ci.csv_text.strip():
        raise HTTPException(400, "Upload or paste an inventory CSV, or switch the source to Shopify")
    case = CounterCase(input=ci)
    store.save(case)
    _spawn(counter_engine.run_counter(case))
    return {"id": case.id}


@app.get("/api/cases/{case_id}", dependencies=[Depends(require_token)])
def get_case(case_id: str):
    return _case_or_404(case_id).model_dump()


@app.post("/api/cases/{case_id}/recheck", dependencies=[Depends(require_token)])
async def recheck(case_id: str):
    case = _case_or_404(case_id)
    if case.status == "running":
        raise HTTPException(409, "Case is already running")
    if case.replay:
        raise HTTPException(409, "Recorded runs can't be re-checked; start a live case")
    if getattr(case, "mode", "defend") != "defend":
        raise HTTPException(409, "Re-check applies to Defend cases")
    _spawn(pipeline.recheck(case))
    return {"ok": True}


@app.get("/api/cases/{case_id}/events", dependencies=[Depends(require_token)])
async def events(case_id: str, request: Request):
    _case_or_404(case_id)
    queue = store.subscribe(case_id)

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            store.unsubscribe(case_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- approval gate ----------

class Decision(BaseModel):
    decision: str            # approve | reject | reset
    text: str | None = None  # edited draft (optional)
    note: str = ""


@app.post("/api/cases/{case_id}/actions/{action_id}", dependencies=[Depends(require_token)])
def decide(case_id: str, action_id: str, d: Decision):
    case = _case_or_404(case_id)
    action = next((a for a in case.actions if a.id == action_id), None)
    if not action:
        raise HTTPException(404, "Action not found")
    if d.decision == "approve":
        final = (d.text if d.text is not None else action.draft).strip()
        flags = _recheck_guardrail(case, action, final)
        if any(f.startswith(counter_engine.TRADEMARK_FLAG) for f in flags):
            # Hard rule: an ad that names the competitor can't be approved, edit it first.
            action.guardrail_flags = flags
            store.save(case)
            raise HTTPException(409, "This ad names the competitor. Remove the competitor's name or product from the copy, then approve.")
        action.guardrail_flags = flags
        action.status = "approved"
        action.final_text = final
    elif d.decision == "reject":
        action.status, action.final_text = "rejected", ""
    elif d.decision == "reset":
        action.status, action.final_text = "pending", ""
    else:
        raise HTTPException(400, "decision must be approve, reject or reset")
    action.decided_at = now_iso() if d.decision != "reset" else ""
    action.decided_note = d.note[:500]
    store.save(case)
    return action.model_dump()


def _recheck_guardrail(case, action, text: str) -> list[str]:
    """Edited text is checked again; the reviewer sees flags for what they actually approved."""
    if action.kind == "ad_package":
        sku = next((s for s in case.skus if s.sku == action.meta.get("sku")), None)
        return counter_engine.guard_ad(text, case.input, sku) if sku else action.guardrail_flags
    allowed = [i.url for i in case.items] + [action.target_url]
    return respond_mod.guardrail(text, case.input, allowed)


@app.get("/api/cases/{case_id}/export.csv", dependencies=[Depends(require_token)])
def export_csv(case_id: str):
    """Only APPROVED actions leave the system."""
    case = _case_or_404(case_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    if getattr(case, "mode", "defend") == "counter":
        # Starting point for an ad-platform bulk import: map columns in the importer. Status is always PAUSED.
        w.writerow(["Campaign Name", "Ad Set Name", "Ad Name", "Status", "Daily Budget (suggested)", "Headline",
                    "Primary Text", "Call to Action", "Website URL", "Audience Ideas", "SKU", "Approved Copy", "Approved At"])
        for a in case.actions:
            if a.status != "approved":
                continue
            m = a.meta
            final = a.final_text
            head = _section(final, "HEADLINES") or (m.get("headlines") or [""])
            body = _section(final, "BODY") or [m.get("body", "")]
            aud = re.search(r"AUDIENCE IDEAS: (.*)", final)
            w.writerow([f"Counter {case.input.our_brand} {m.get('friction', '')}".strip(), f"{m.get('sku')} {m.get('friction', '')}",
                        a.title, "PAUSED", m.get("suggested_daily_budget", ""), head[0], " ".join(body),
                        m.get("cta", ""), a.target_url, aud.group(1) if aud else "", m.get("sku"), final, a.decided_at])
    else:
        w.writerow(["kind", "platform", "target_url", "title", "approved_text", "approved_at", "reviewer_note"])
        for a in case.actions:
            if a.status == "approved":
                w.writerow([a.kind, a.platform, a.target_url, a.title, a.final_text, a.decided_at, a.decided_note])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="contagion-{case_id}-approved.csv"'})


def _section(text: str, name: str) -> list[str]:
    m = re.search(rf"{name}\n(.*?)(?:\n\n|$)", text, re.S)
    if not m:
        return []
    return [ln.lstrip("- ").strip() for ln in m.group(1).splitlines() if ln.strip()]


@app.get("/api/cases/{case_id}/export.json", dependencies=[Depends(require_token)])
def export_json(case_id: str):
    case = _case_or_404(case_id)
    return JSONResponse(case.model_dump(),
                        headers={"Content-Disposition": f'attachment; filename="contagion-{case_id}.json"'})


# ---------- replays (stage insurance) ----------

class ReplayName(BaseModel):
    name: str


@app.post("/api/cases/{case_id}/save-replay", dependencies=[Depends(require_token)])
def save_replay(case_id: str, body: ReplayName):
    case = _case_or_404(case_id)
    if case.status != "done" and getattr(case, "mode", "") != "watch":
        raise HTTPException(409, "Only finished runs can be saved as replays")
    safe = "".join(ch for ch in body.name.lower() if ch.isalnum() or ch in "-_")[:60] or case.id
    (store.REPLAYS_DIR / f"{safe}.json").write_text(case.model_dump_json(indent=1))
    return {"name": safe}


@app.post("/api/replays/{name}", dependencies=[Depends(require_token)])
def open_replay(name: str):
    path = store.REPLAYS_DIR / f"{Path(name).name}.json"
    if not path.exists():
        raise HTTPException(404, "Replay not found")
    case = store._load(path.read_text())
    recorded_at = case.created_at
    update = {"id": short_id(), "replay": True}
    if getattr(case, "mode", "") == "watch":
        update["status"] = "stopped"
    case = case.model_copy(update=update)
    case.log.insert(0, LogLine(stage="replay", message=f"Recorded run from {recorded_at}, opened as a replay. No live calls were made.", level="warn"))
    store.save(case)
    return {"id": case.id}


@app.get("/api/samples/inventory.csv", dependencies=[Depends(require_token)])
def sample_inventory():
    return PlainTextResponse((ROOT / "data" / "samples" / "inventory_sample.csv").read_text(), media_type="text/csv")


# ---------- watch (live brand monitor) ----------

def _watch_or_404(watch_id: str) -> Watch:
    w = store.get(watch_id)
    if not isinstance(w, Watch):
        raise HTTPException(404, "Watch not found")
    return w


@app.post("/api/watch", dependencies=[Depends(require_token)])
async def create_watch(wi: WatchInput):
    wi.keywords = [k.strip() for k in wi.keywords if k.strip()][:5]
    w = Watch(input=wi, poll_seconds=watch_engine.POLL_SECONDS)
    store.save(w)
    watch_engine.start(w)
    return {"id": w.id}


@app.get("/api/watch/{watch_id}", dependencies=[Depends(require_token)])
def get_watch(watch_id: str):
    w = _watch_or_404(watch_id)
    d = w.model_dump()
    d["live"] = watch_engine.is_running(w.id)
    d["channels"] = watch_engine.channels()
    return d


@app.post("/api/watch/{watch_id}/test-alert", dependencies=[Depends(require_token)])
async def watch_test_alert(watch_id: str):
    """Send the current top threat to the team channels, so you can see the alert land."""
    from .watch.models import Alert
    w = _watch_or_404(watch_id)
    if not watch_engine.channels():
        raise HTTPException(400, "No alert channel set. Add SLACK_WEBHOOK_URL or ALERT_WEBHOOK_URL in the environment.")
    top = next((n for n in w.narratives if n.threat_type != "praise"), None)
    a = Alert(narrative_id=top.id if top else "", level="critical" if top and top.score >= 70 else "warn",
              title=f"Top threat: {top.title}" if top else "Contagion test alert",
              reason=(f"Threat score {top.score}/100, {top.count} mentions on {', '.join(top.platforms)}." if top else "Channel check."))
    await watch_engine.deliver(w, [a])
    return {"delivered": a.delivered}


class WatchControl(BaseModel):
    command: str   # pause | resume | poll | stop


@app.post("/api/watch/{watch_id}/control", dependencies=[Depends(require_token)])
def control_watch(watch_id: str, c: WatchControl):
    w = _watch_or_404(watch_id)
    if w.replay:
        raise HTTPException(409, "Recorded runs are read-only; start a live watch")
    if c.command == "pause":
        w.status = "paused"
    elif c.command in ("resume", "poll"):
        if w.status in ("paused", "stopped", "failed"):
            w.status = "running"
        watch_engine.start(w)
        watch_engine.poke(w.id)
    elif c.command == "stop":
        w.status = "stopped"
        watch_engine.poke(w.id)
    else:
        raise HTTPException(400, "command must be pause, resume, poll or stop")
    store.save(w)
    store.publish(w.id, {"type": "update", "stage": w.stage})
    return {"status": w.status}


class PositionBody(BaseModel):
    position: str = ""


@app.post("/api/watch/{watch_id}/position", dependencies=[Depends(require_token)])
def set_position(watch_id: str, body: PositionBody):
    w = _watch_or_404(watch_id)
    w.input.position = body.position[:2000]
    store.save(w)
    return {"ok": True}


@app.post("/api/watch/{watch_id}/narratives/{nid}/playbook", dependencies=[Depends(require_token)])
async def watch_playbook(watch_id: str, nid: str):
    w = _watch_or_404(watch_id)
    n = next((x for x in w.narratives if x.id == nid), None)
    if not n:
        raise HTTPException(404, "Narrative not found")
    await watch_engine.make_playbook(w, n)
    return {"ok": True}


class TraceBody(BaseModel):
    claim: str
    truth: str


@app.post("/api/watch/{watch_id}/narratives/{nid}/trace", dependencies=[Depends(require_token)])
async def watch_trace(watch_id: str, nid: str, body: TraceBody):
    w = _watch_or_404(watch_id)
    n = next((x for x in w.narratives if x.id == nid), None)
    if not n:
        raise HTTPException(404, "Narrative not found")
    ci = CaseInput(brand=w.input.brand, claim=body.claim, truth=body.truth,
                   truth_url=f"https://{w.input.domain}" if w.input.domain else "")
    case = Case(input=ci)
    store.save(case)
    n.trace_case_id = case.id
    store.save(w)
    _spawn(pipeline.run_case(case))
    return {"id": case.id}


@app.post("/api/watch/{watch_id}/actions/{action_id}", dependencies=[Depends(require_token)])
def watch_decide(watch_id: str, action_id: str, d: Decision):
    w = _watch_or_404(watch_id)
    a = next((x for x in w.actions if x.id == action_id), None)
    if not a:
        raise HTTPException(404, "Action not found")
    if d.decision == "approve":
        final = (d.text if d.text is not None else a.draft).strip()
        from .watch.playbook import guardrail
        allowed = {m.url for m in w.mentions} | ({a.target_url} if a.target_url else set())
        a.flags = guardrail(final, w, allowed)
        a.status, a.final_text = "approved", final
    elif d.decision == "reject":
        a.status, a.final_text = "rejected", ""
    elif d.decision == "reset":
        a.status, a.final_text = "pending", ""
    else:
        raise HTTPException(400, "decision must be approve, reject or reset")
    a.decided_at = now_iso() if d.decision != "reset" else ""
    a.decided_note = d.note[:500]
    store.save(w)
    return a.model_dump()


@app.post("/api/watch/{watch_id}/alerts/read", dependencies=[Depends(require_token)])
def watch_alerts_read(watch_id: str):
    w = _watch_or_404(watch_id)
    for a in w.alerts:
        a.read = True
    store.save(w)
    return {"ok": True}


@app.get("/api/watch/{watch_id}/export.csv", dependencies=[Depends(require_token)])
def watch_export(watch_id: str):
    """Only APPROVED actions leave the system."""
    w = _watch_or_404(watch_id)
    titles = {n.id: n.title for n in w.narratives}
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["narrative", "kind", "title", "owner", "timing", "channel", "target_url", "approved_text", "approved_at", "reviewer_note"])
    for a in w.actions:
        if a.status == "approved":
            wr.writerow([titles.get(a.narrative_id, ""), a.kind, a.title, a.owner, a.timing, a.channel, a.target_url,
                         a.final_text, a.decided_at, a.decided_note])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="contagion-watch-{watch_id}-approved.csv"'})


# ---------- static UI ----------

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    return FileResponse(WEB_DIR / "index.html")
