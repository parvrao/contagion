"""The four-stage run: Discovery > Analysis > Cross-referencing > Synthesis.

Stages fail soft: a dead source or engine is logged and reported in the UI,
the rest of the run continues. Only a crash in our own code fails the case.
"""
from __future__ import annotations

import asyncio
import time
import traceback

from . import analyze, respond, spread, store
from .config import settings
from .models import Case, Item, LogLine, Probe, now_iso
from .probes import all_engines, profound
from .sources import all_sources
from .sources.base import SearchPlan, domain_of

STAGES = ["discovery", "analysis", "cross_reference", "synthesis"]


def _log(case: Case, stage: str, message: str, level: str = "info") -> None:
    line = LogLine(stage=stage, message=message, level=level)
    case.log.append(line)
    store.publish(case.id, {"type": "log", "line": line.model_dump(), "stage": case.stage})


def _stage(case: Case, stage: str) -> None:
    case.stage = stage
    store.save(case)
    store.publish(case.id, {"type": "stage", "stage": stage})


async def run_case(case: Case) -> None:
    t0 = time.monotonic()
    case.status = "running"
    try:
        questions = await discovery(case)
        await analysis(case)
        await cross_reference(case, questions)
        await synthesis(case, questions)
        case.status = "done"
        case.stage = "done"
        _log(case, "synthesis", f"Run complete: Grade {case.grade['level']} ({case.grade['label']}), risk {case.grade['risk_score']}/100.")
    except Exception as exc:  # our own bug: surface it, don't hide it
        case.status = "failed"
        _log(case, case.stage, f"Run failed: {exc}", "error")
        traceback.print_exc()
    finally:
        case.elapsed_seconds = round(time.monotonic() - t0, 1)
        store.save(case)
        store.publish(case.id, {"type": "done", "status": case.status})


# ---------- Stage 1 ----------

async def discovery(case: Case) -> list[str]:
    _stage(case, "discovery")
    ci = case.input
    queries, questions = await analyze.plan_queries(ci)
    case.spread["queries"] = queries
    case.spread["questions"] = questions
    _log(case, "discovery", f"Search plan: {len(queries)} phrasings ({'; '.join(queries[:4])}...)")

    plan = SearchPlan(brand=ci.brand, claim=ci.claim, queries=queries, since=ci.since, until=ci.until,
                      limit=settings.max_items_per_source)

    async def run_source(src):
        ok, reason = src.enabled()
        if not ok:
            case.sources_status[src.platform] = f"skipped: {reason}"
            _log(case, "discovery", f"{src.platform}: skipped ({reason})", "warn")
            return []
        try:
            found = await src.search(plan)
            case.sources_status[src.platform] = f"ok ({len(found)})"
            _log(case, "discovery", f"{src.platform}: {len(found)} public items")
            return found
        except Exception as exc:
            case.sources_status[src.platform] = f"error: {str(exc)[:140]}"
            _log(case, "discovery", f"{src.platform}: failed ({str(exc)[:140]})", "warn")
            return []

    results = await asyncio.gather(*[run_source(s) for s in all_sources()])
    merged: dict[str, Item] = {}
    for batch in results:
        for it in batch:
            key = spread.norm_url(it.url)
            if key not in merged:
                merged[key] = it
    case.items = list(merged.values())
    _log(case, "discovery", f"{len(case.items)} unique public items collected across {sum(1 for r in results if r)} sources")
    store.save(case)
    return questions


# ---------- Stage 2 ----------

async def analysis(case: Case) -> None:
    _stage(case, "analysis")
    await analyze.classify(case.items, case.input)
    counts = {s: sum(1 for i in case.items if i.stance == s) for s in ("amplifies", "debunks", "reports", "unrelated")}
    _log(case, "analysis", "Labeled: {amplifies} spreading, {debunks} correcting, {reports} neutral, {unrelated} unrelated".format(**counts))
    overturned = await analyze.skeptic(case.items, case.input)
    _log(case, "analysis", f"Skeptic review: {overturned} 'spreading' label(s) overturned; only confirmed ones count")
    case.spread.update(spread.analyze_spread(case.items))
    s = case.spread
    if s.get("earliest_amplifier_id"):
        first = next(i for i in case.items if i.id == s["earliest_amplifier_id"])
        _log(case, "analysis", f"Earliest public instance found: {first.platform}, {first.published_at[:10]} ({first.author or first.domain})")
    if s.get("correction_lag_hours") is not None:
        _log(case, "analysis", f"Correction lag: first correction appeared {s['correction_lag_hours']} h after the earliest spreading post")
    store.save(case)


# ---------- Stage 3 ----------

async def cross_reference(case: Case, questions: list[str]) -> None:
    _stage(case, "cross_reference")
    case.probes = await probe_engines(case, questions)
    contamination = spread.cross_reference(case.probes, case.items)
    case.spread["contamination"] = contamination
    _log(case, "cross_reference",
         f"AI answers: {contamination['repeats']} repeat the rumor, {contamination['corrects']} correct it, "
         f"{contamination['unaware']} don't mention it (of {contamination['answers']})")
    if contamination["traced_pages_cited"]:
        _log(case, "cross_reference", f"{contamination['traced_pages_cited']} traced page(s) are being cited by AI engines")

    ok, reason = profound.enabled()
    if ok:
        hosts = sorted({(i.domain or domain_of(i.url)) for i in case.items if i.stance == "amplifies"} - {""})
        try:
            rows = await profound.citations_for(hosts)
            case.spread["profound"] = {"status": "ok", "rows": rows}
            _log(case, "cross_reference", f"Profound: {len(rows)} citation rows for {len(hosts)} rumor-carrying domains")
        except Exception as exc:
            case.spread["profound"] = {"status": f"error: {str(exc)[:160]}", "rows": []}
            _log(case, "cross_reference", f"Profound: failed ({str(exc)[:160]})", "warn")
    else:
        case.spread["profound"] = {"status": f"skipped: {reason}", "rows": []}
    store.save(case)


async def probe_engines(case: Case, questions: list[str]) -> list[Probe]:
    engines = all_engines()
    active = []
    for e in engines:
        ok, reason = e.enabled()
        label = f"{e.name} ({e.mode})"
        if ok:
            active.append(e)
        else:
            case.sources_status[label] = f"skipped: {reason}"
    _log(case, "cross_reference", f"Asking {len(active)} AI engine setup(s) {len(questions)} neutral question(s) each")

    async def one(engine, q):
        p = Probe(engine=engine.name, mode=engine.mode, question=q)
        try:
            p.answer, p.citations = await engine.ask(q)
        except Exception as exc:
            p.error = str(exc)[:200]
        await analyze.judge_probe(p, case.input)
        return p

    probes = await asyncio.gather(*[one(e, q) for e in active for q in questions])
    for e in active:
        errs = [p for p in probes if p.engine == e.name and p.mode == e.mode and p.error]
        case.sources_status[f"{e.name} ({e.mode})"] = f"error: {errs[0].error[:120]}" if errs else "ok"
    return list(probes)


# ---------- Stage 4 ----------

async def synthesis(case: Case, questions: list[str]) -> None:
    _stage(case, "synthesis")
    case.grade = spread.grade(case.spread, case.spread["contamination"], case.items)
    case.history.append({"at": now_iso(), "kind": "initial run", **_snapshot(case)})
    case.actions = await respond.build_actions(case.input, case.items, case.spread["contamination"], questions)
    _log(case, "synthesis", f"Drafted {len(case.actions)} actions. All are waiting for human approval; nothing has been sent.")
    store.save(case)


def _snapshot(case: Case) -> dict:
    c = case.spread.get("contamination", {})
    return {"level": case.grade["level"], "label": case.grade["label"], "risk_score": case.grade["risk_score"],
            "repeats": c.get("repeats", 0), "answers": c.get("answers", 0)}


async def recheck(case: Case) -> None:
    """Re-ask the AI engines only (the slow-moving, high-stakes part) and compare."""
    case.status = "running"
    _stage(case, "cross_reference")
    _log(case, "cross_reference", "Re-check started: asking the AI engines again")
    try:
        questions = case.spread.get("questions") or []
        case.probes = await probe_engines(case, questions)
        case.spread["contamination"] = spread.cross_reference(case.probes, case.items)
        before = case.history[-1] if case.history else None
        case.grade = spread.grade(case.spread, case.spread["contamination"], case.items)
        case.history.append({"at": now_iso(), "kind": "re-check", **_snapshot(case)})
        delta = f" (was Grade {before['level']}, {before['repeats']}/{before['answers']} repeating)" if before else ""
        _log(case, "synthesis", f"Re-check: Grade {case.grade['level']}, {case.spread['contamination']['repeats']}/"
                                f"{case.spread['contamination']['answers']} answers repeating{delta}")
        case.status = "done"
        case.stage = "done"
    except Exception as exc:
        case.status = "failed"
        _log(case, "synthesis", f"Re-check failed: {exc}", "error")
    finally:
        store.save(case)
        store.publish(case.id, {"type": "done", "status": case.status})
