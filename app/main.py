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

ACCESS_TOKEN = os.environ.get("CONTAGION_ACCESS_TOKEN", "").strip()
WEB_DIR = ROOT / "web"
_running: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
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
    if case.status != "done":
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
    case = case.model_copy(update={"id": short_id(), "replay": True})
    case.log.insert(0, LogLine(stage="replay", message=f"Recorded run from {recorded_at}, opened as a replay. No live calls were made.", level="warn"))
    store.save(case)
    return {"id": case.id}


@app.get("/api/samples/inventory.csv", dependencies=[Depends(require_token)])
def sample_inventory():
    return PlainTextResponse((ROOT / "data" / "samples" / "inventory_sample.csv").read_text(), media_type="text/csv")


# ---------- static UI ----------

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    return FileResponse(WEB_DIR / "index.html")
