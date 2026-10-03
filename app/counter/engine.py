"""Counter pipeline: Inventory > Listening > Reality check > Match & draft.

Hard rules:
- No ad is drafted on a complaint cluster that isn't verified (first-hand, multi-platform).
  Advertising against an unverified or false claim about a competitor is a
  false-advertising risk, and it's exactly what Contagion exists to stop.
- Feature claims in ads must quote the SKU's own description (checked by substring).
- No competitor names or trademarks in copy (guardrail flag).
- Every ad package starts pending. Export only. Nothing is published or spent.
"""
from __future__ import annotations

import asyncio
import re
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import llm, spread, store
from ..config import settings
from ..models import Action, LogLine
from ..sources import all_sources
from ..sources.base import SearchPlan
from . import inventory
from .models import Cluster, CounterCase, Match

STAGES = ["inventory", "listening", "reality", "drafting"]
FRICTIONS = ["durability", "quality defect", "price/value", "fit/comfort", "performance", "safety",
             "shipping/stock", "customer support", "other"]
MIN_FIRST_HAND = 3
MIN_PLATFORMS = 2


def _log(case: CounterCase, stage: str, msg: str, level: str = "info") -> None:
    line = LogLine(stage=stage, message=msg, level=level)
    case.log.append(line)
    store.publish(case.id, {"type": "log", "line": line.model_dump(), "stage": case.stage})


def _stage(case: CounterCase, stage: str) -> None:
    case.stage = stage
    store.save(case)
    store.publish(case.id, {"type": "stage", "stage": stage})


async def run_counter(case: CounterCase) -> None:
    t0 = time.monotonic()
    case.status = "running"
    try:
        await stage_inventory(case)
        await stage_listening(case)
        await stage_reality(case)
        await stage_drafting(case)
        case.status, case.stage = "done", "done"
        _log(case, "drafting", f"Done: {len(case.actions)} ad package(s) waiting for approval. Nothing has been published or spent.")
    except inventory.InventoryError as exc:
        case.status = "failed"
        _log(case, case.stage, str(exc), "error")
    except Exception as exc:
        case.status = "failed"
        _log(case, case.stage, f"Run failed: {exc}", "error")
        traceback.print_exc()
    finally:
        case.elapsed_seconds = round(time.monotonic() - t0, 1)
        store.save(case)
        store.publish(case.id, {"type": "done", "status": case.status})


# ---------- 1. Inventory ----------

async def stage_inventory(case: CounterCase) -> None:
    _stage(case, "inventory")
    ci = case.input
    if ci.inventory_source == "shopify":
        ok, why = inventory.shopify_configured()
        if not ok:
            raise inventory.InventoryError(f"Shopify not configured: {why}. Or switch the source to CSV.")
        case.skus = await inventory.load_shopify()
        case.sources_status["Shopify"] = f"ok ({len(case.skus)} variants)"
    else:
        case.skus = inventory.load_csv(ci.csv_text)
        case.sources_status["Inventory CSV"] = f"ok ({len(case.skus)} rows)"
    inventory.apply_rules(case.skus, ci)
    flagged = [s for s in case.skus if s.flagged]
    case.summary.update(skus=len(case.skus), surplus_skus=len(flagged), surplus_units=sum(s.surplus_units for s in flagged))
    case.summary["impact"] = inventory.impact(case.skus, ci)
    imp = case.summary["impact"]
    if imp["stockout_guard"]:
        _log(case, "inventory", f"Stockout guard: {len(imp['stockout_guard'])} fast mover(s) excluded from ads ("
                                + ", ".join(x["sku"] for x in imp["stockout_guard"]) + ")")
    _log(case, "inventory", f"{len(case.skus)} SKUs loaded; {len(flagged)} flagged as surplus with margin room "
                            f"({case.summary['surplus_units']} units above the {ci.dir_threshold_days:g}-day target)")
    if not flagged:
        _log(case, "inventory", "No SKU meets the surplus + margin rule; listening still runs so you can see the market gap.", "warn")
    store.save(case)


# ---------- 2. Listening ----------

async def stage_listening(case: CounterCase) -> None:
    _stage(case, "listening")
    ci = case.input
    target = f"{ci.competitor} {ci.competitor_product}".strip()
    queries = await _plan(ci, target)
    _log(case, "listening", f"Listening for complaints about {target}: {'; '.join(queries[:4])}")
    plan = SearchPlan(brand=ci.competitor, claim=f"customer complaints about {target}", queries=queries,
                      since=ci.since, until=ci.until, limit=settings.max_items_per_source)

    async def run(src):
        ok, reason = src.enabled()
        if not ok:
            case.sources_status[src.platform] = f"skipped: {reason}"
            return []
        try:
            got = await src.search(plan)
            case.sources_status[src.platform] = f"ok ({len(got)})"
            return got
        except Exception as exc:
            case.sources_status[src.platform] = f"error: {str(exc)[:140]}"
            _log(case, "listening", f"{src.platform}: failed ({str(exc)[:120]})", "warn")
            return []

    merged = {}
    for batch in await asyncio.gather(*[run(s) for s in all_sources()]):
        for it in batch:
            merged.setdefault(spread.norm_url(it.url), it)
    case.items = list(merged.values())
    _log(case, "listening", f"{len(case.items)} public items collected")
    await _label_complaints(case)
    case.clusters = _cluster(case)
    case.summary.update(complaints=sum(c.mentions for c in case.clusters), clusters=len(case.clusters))
    _log(case, "listening", f"{case.summary['complaints']} complaint(s) in {len(case.clusters)} friction cluster(s): "
                            + ", ".join(f"{c.friction} ({c.mentions})" for c in case.clusters[:5]))
    store.save(case)


async def _plan(ci, target: str) -> list[str]:
    base = [f"{target} problem", f"{target} complaints", f"{target} review"] + ci.keywords
    if not llm.available():
        return base
    try:
        data = await llm.complete_json(
            "You plan social listening searches for customer complaints about a product.",
            f"Product: {target}\nCategory: {ci.category or 'unknown'}\n"
            'Return {"queries": [6 short searches (2 to 6 words) real customers would post when unhappy; include the product name]}',
            max_tokens=400,
        )
        q = [x for x in data.get("queries", []) if isinstance(x, str)]
        return list(dict.fromkeys(ci.keywords + q))[:8] or base
    except Exception:
        return base


async def _label_complaints(case: CounterCase) -> None:
    ci = case.input
    target = f"{ci.competitor} {ci.competitor_product}".strip()
    if not llm.available():
        for it in case.items:
            it.stance, it.stance_reason = "unknown", "no model: complaints not labeled"
        return

    async def batch(rows):
        text = "\n".join(f"[{i}] ({it.platform}) {it.title} :: {it.text[:400]}".replace("\n", " ") for i, it in enumerate(rows))
        try:
            data = await llm.complete_json(
                "You label public posts about a product. For each item decide:\n"
                "complaint: is it a complaint about THIS product (not the category in general)?\n"
                f"friction: one of {FRICTIONS}\n"
                "first_hand: does the author describe their own experience (owned/used it)?\n"
                "rumor: is it repeating an unverified claim or something 'people are saying'?\n"
                "quote: the most telling sentence, verbatim, under 25 words, or empty.",
                f"Product: {target}\n\nItems:\n{text}\n\n"
                'Return [{"i": n, "complaint": bool, "friction": str, "first_hand": bool, "rumor": bool, "quote": str}]',
                max_tokens=3000,
            )
            by_i = {int(r["i"]): r for r in data if isinstance(r, dict) and "i" in r}
        except Exception:
            by_i = {}
        for i, it in enumerate(rows):
            r = by_i.get(i) or {}
            if r.get("complaint"):
                it.stance = "amplifies"   # reused field: 'complaint about competitor'
                fr = r.get("friction") if r.get("friction") in FRICTIONS else "other"
                tags = [fr, "first-hand" if r.get("first_hand") else "second-hand"] + (["rumor"] if r.get("rumor") else [])
                it.stance_reason = "|".join(tags)
                it.confirmed = bool(r.get("first_hand")) and not r.get("rumor")
                it.quote = str(r.get("quote") or "")[:240]
            else:
                it.stance, it.stance_reason = "unrelated", "not a complaint about this product"

    rows = case.items
    await asyncio.gather(*[batch(rows[i:i + 20]) for i in range(0, len(rows), 20)])


def _reference_time(ci) -> datetime:
    """'Now' for spike math: the end of the analysis window if one is set (historical demos), else now."""
    if ci.until:
        try:
            return datetime.strptime(ci.until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _cluster(case: CounterCase) -> list[Cluster]:
    now = _reference_time(case.input)
    groups = defaultdict(list)
    for it in case.items:
        if it.stance == "amplifies":
            groups[it.stance_reason.split("|")[0]].append(it)
    out = []
    for fr, items in groups.items():
        c = Cluster(friction=fr, item_ids=[i.id for i in items], mentions=len(items),
                    first_hand=sum(1 for i in items if i.confirmed),
                    platforms=sorted({i.platform for i in items}),
                    engagement=sum(i.engagement for i in items))
        recent_eng = base_eng = base_n = 0
        for i in items:
            try:
                age = now - datetime.fromisoformat(i.published_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if age < timedelta(0):
                continue
            if age <= timedelta(days=7):
                c.recent_7d += 1
                recent_eng += i.engagement
            elif age <= timedelta(days=35):
                base_n += 1
                base_eng += i.engagement
                if age <= timedelta(days=14):
                    c.prior_7d += 1
        c.baseline_week = round(base_n / 4, 2)
        c.spike = round((c.recent_7d + 1) / (c.baseline_week + 1), 2)
        c.engagement_spike = round(recent_eng / (base_eng / 4), 2) if base_eng else None
        c.new_signal = c.recent_7d >= 3 and base_n == 0
        c.spiking = c.recent_7d >= 3 and base_n > 0 and (c.spike >= 2 or (c.engagement_spike or 0) >= 3)
        yt = [i for i in items if i.platform == "YouTube" and i.views]
        if yt:
            c.comment_to_view = round(sum(i.comments for i in yt) / sum(i.views for i in yt), 5)
        top = sorted(items, key=lambda i: (not i.confirmed, -i.engagement))[:4]
        c.quotes = [{"text": i.quote or (i.title or i.text)[:200],
                     "url": i.url, "platform": i.platform, "first_hand": i.confirmed} for i in top]
        out.append(c)
    out.sort(key=lambda c: (-(c.spiking or c.new_signal), -c.first_hand, -c.mentions))
    return out


# ---------- 3. Reality check ----------

async def stage_reality(case: CounterCase) -> None:
    _stage(case, "reality")
    for c in case.clusters:
        items = [i for i in case.items if i.id in c.item_ids]
        rumorish = sum(1 for i in items if "rumor" in i.stance_reason)
        if c.first_hand >= MIN_FIRST_HAND and len(c.platforms) >= MIN_PLATFORMS:
            c.reality = "verified"
            c.reality_reason = f"{c.first_hand} first-hand reports across {len(c.platforms)} platforms"
        elif rumorish and rumorish >= c.first_hand:
            c.reality = "disputed"
            c.reality_reason = f"{rumorish} of {c.mentions} mentions repeat unverified claims; not safe to advertise against"
        else:
            c.reality = "unverified"
            c.reality_reason = (f"only {c.first_hand} first-hand report(s) on {len(c.platforms)} platform(s); "
                                f"needs {MIN_FIRST_HAND}+ on {MIN_PLATFORMS}+")
    v = [c for c in case.clusters if c.reality == "verified"]
    case.summary["verified_clusters"] = len(v)
    _log(case, "reality", f"{len(v)} of {len(case.clusters)} cluster(s) verified. Only verified clusters can drive ads.")
    store.save(case)


# ---------- competitor facts ----------

def usable_facts(ci, today: datetime | None = None) -> tuple[list[dict], list[str]]:
    """Facts with a source and a recent check date. Stale or undated facts are refused, not silently used."""
    today = today or datetime.now(timezone.utc)
    ok, problems = [], []
    for f in ci.competitor_facts:
        try:
            checked = datetime.strptime(f.checked_on, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            problems.append(f"'{f.fact}': bad date {f.checked_on}")
            continue
        age = (today - checked).days
        if age > ci.fact_max_age_days:
            problems.append(f"'{f.fact}' checked {age} days ago (max {ci.fact_max_age_days}); re-check before using")
        elif not f.source_url.startswith(("http://", "https://")):
            problems.append(f"'{f.fact}': source must be a URL")
        else:
            ok.append(f.model_dump())
    return ok, problems


def build_brief(case: CounterCase, c: Cluster, s, m: Match, facts: list[dict]) -> dict:
    """The structured payload the drafting model receives. Shown verbatim in the UI."""
    ci = case.input
    return {
        "competitor_weakness": {
            "friction": c.friction, "reality": c.reality, "why": c.reality_reason,
            "mentions": c.mentions, "first_hand": c.first_hand, "platforms": c.platforms,
            "last_7_days": c.recent_7d, "baseline_per_week": c.baseline_week, "spike": c.spike,
            "engagement_spike": c.engagement_spike, "new_this_week": c.new_signal, "comment_to_view": c.comment_to_view,
            "customer_quotes": [q["text"] for q in c.quotes[:3]],
        },
        "candidate_sku": {
            "sku": s.sku, "name": f"{s.name} {s.variant}".strip(), "price": s.price,
            "units_on_hand": s.units_on_hand, "surplus_units": s.surplus_units,
            "days_of_supply": s.days_of_inventory, "margin_pct": s.margin_pct, "max_cac": s.max_cac,
            "verbatim_strength": m.evidence, "product_copy": s.description,
        },
        "verified_competitor_facts": facts,
        "rules": {
            "no_competitor_names_or_trademarks": True,
            "product_claims_only_from": "candidate_sku.product_copy",
            "comparisons_only_from": "verified_competitor_facts (exact values)",
            "no_other_numbers": True,
        },
    }


# ---------- 4. Match & draft ----------

async def stage_drafting(case: CounterCase) -> None:
    _stage(case, "drafting")
    clusters = [c for c in case.clusters if c.reality == "verified"]
    skus = [s for s in case.skus if s.flagged]
    if not clusters or not skus:
        _log(case, "drafting", "Nothing to draft: needs at least one verified cluster and one surplus SKU.", "warn")
        return
    facts, problems = usable_facts(case.input)
    for p in problems:
        _log(case, "drafting", f"Competitor fact refused: {p}", "warn")
    if facts:
        _log(case, "drafting", f"{len(facts)} verified competitor fact(s) available for comparison copy")
    case.summary["facts_used"], case.summary["facts_refused"] = len(facts), problems
    case.matches = await _match(case, clusters, skus)
    for m in case.matches:
        cl = next(x for x in clusters if x.id == m.cluster_id)
        sk = next(x for x in skus if x.sku == m.sku)
        m.brief = build_brief(case, cl, sk, m, facts)
    _log(case, "drafting", f"{len(case.matches)} cluster-to-SKU match(es) backed by the SKU's own description")
    for m in case.matches[:6]:
        action = await _draft(case, m)
        if action:
            case.actions.append(action)
    case.summary["ad_packages"] = len(case.actions)
    store.save(case)


async def _match(case, clusters, skus) -> list[Match]:
    if not llm.available():
        return []
    cl = "\n".join(f"[{c.id}] {c.friction}: " + " / ".join(q["text"] for q in c.quotes[:3]) for c in clusters)
    sk = "\n".join(f"[{s.sku}] {s.name} {s.variant}: {s.description[:600]}" for s in skus)
    try:
        data = await llm.complete_json(
            "You match a competitor's verified customer pain points to our products. A match is valid ONLY if our "
            "product description explicitly states a strength that answers the pain point. 'evidence' must be copied "
            "verbatim from the product description. No match is better than a weak one.",
            f"Pain points:\n{cl}\n\nOur products:\n{sk}\n\n"
            'Return [{"cluster_id": str, "sku": str, "fit": 0..1, "evidence": "verbatim phrase", "angle": "one line"}]',
            max_tokens=1500,
        )
    except Exception:
        return []
    by_sku = {s.sku: s for s in skus}
    ids = {c.id for c in clusters}
    out = []
    for r in data if isinstance(data, list) else []:
        s = by_sku.get(str(r.get("sku")))
        ev = str(r.get("evidence", "")).strip()
        if not s or r.get("cluster_id") not in ids or not ev:
            continue
        if _norm(ev) not in _norm(s.description):   # anti-hallucination: evidence must be real copy
            continue
        out.append(Match(cluster_id=r["cluster_id"], sku=s.sku, fit=float(r.get("fit") or 0), evidence=ev,
                         angle=str(r.get("angle", ""))[:200]))
    out.sort(key=lambda m: -m.fit)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


async def _draft(case: CounterCase, m: Match) -> Action | None:
    ci = case.input
    c = next(x for x in case.clusters if x.id == m.cluster_id)
    s = next(x for x in case.skus if x.sku == m.sku)
    import json as _json
    try:
        data = await llm.complete_json(
            "You write paid social ad drafts from a structured brief. Rules: never name or allude to the competitor brand "
            "or product by name, logo or trademark. Product claims may ONLY come from candidate_sku.product_copy. "
            "Comparisons (e.g. price) may ONLY use verified_competitor_facts, with the exact value, phrased generically "
            "(e.g. 'paying $180 for wet socks?'); if there are none, make no comparisons and no claims about competitors. "
            "No other numbers. Speak to the customer's frustration in their own words. Bold, plain, specific; "
            "no cliches like 'game-changer' or 'elevate'.",
            f"Our brand: {ci.our_brand}\nCategory: {ci.category}\n\nBRIEF (JSON):\n{_json.dumps(m.brief, indent=1)}\n\n"
            'Return {"hooks": [3 short], "headlines": [3, max 40 chars], "body": "max 125 chars", '
            '"cta": "one of Shop Now, Learn More, Get Offer", "audience_ideas": [3 interest/behavior ideas, no competitor brand names]}',
            max_tokens=800,
        )
    except Exception as exc:
        _log(case, "drafting", f"Draft failed for {s.sku}: {exc}", "warn")
        return None

    daily = (s.units_sold_window / s.window_days) if s.window_days else 0
    clear_per_day = s.surplus_units / ci.campaign_days if ci.campaign_days else 0
    budget = round(min(clear_per_day * (s.max_cac or 0), 500.0), 2)  # capped; a human sets the real number
    hooks, heads = data.get("hooks", [])[:3], data.get("headlines", [])[:3]
    text = (
        "HOOKS\n" + "\n".join(f"- {h}" for h in hooks)
        + "\n\nHEADLINES\n" + "\n".join(f"- {h}" for h in heads)
        + f"\n\nBODY\n{data.get('body', '')}\n\nCTA: {data.get('cta', 'Shop Now')}"
        + "\nAUDIENCE IDEAS: " + ", ".join(data.get("audience_ideas", [])[:3])
        + f"\nLANDING: {s.url or ci.landing_url or '[add landing URL]'}"
    )
    flags = guard_ad(text, ci, s)
    used_facts = [f for f in m.brief.get("verified_competitor_facts", []) if f["value"] in text]
    return Action(
        kind="ad_package",
        title=f"{s.name} {s.variant}".strip() + f" vs. {c.friction} complaints",
        target_url=s.url or ci.landing_url,
        draft=text,
        rationale=f"{c.reality_reason}; matched on \"{m.evidence}\"",
        guardrail_flags=flags,
        meta={
            "sku": s.sku, "cluster_id": c.id, "friction": c.friction, "fit": m.fit,
            "units_on_hand": s.units_on_hand, "surplus_units": s.surplus_units, "days_of_inventory": s.days_of_inventory,
            "margin_pct": s.margin_pct, "max_cac": s.max_cac, "suggested_daily_budget": budget,
            "budget_note": f"{s.surplus_units} surplus units / {ci.campaign_days} days x max CAC {s.max_cac}, capped at 500. Heuristic: set the real budget yourself.",
            "cta": data.get("cta", "Shop Now"), "headlines": heads, "body": data.get("body", ""),
            "baseline_daily_sales": round(daily, 2),
            "brief": m.brief,
            "substantiation": [f"{f['fact']}: {f['value']} ({f['source_url']}, checked {f['checked_on']})" for f in used_facts],
            "spike": c.spike, "spiking": c.spiking,
        },
    )


TRADEMARK_FLAG = "mentions competitor term"


def guard_ad(text: str, ci, s) -> list[str]:
    flags = []
    no_urls = re.sub(r"https?://\S+", " ", text)
    for token in {ci.competitor, ci.competitor_product}:
        for word in [w for w in re.split(r"\s+", token or "") if len(w) > 2]:
            if re.search(rf"\b{re.escape(word)}\b", no_urls, re.I) and word.lower() not in ci.our_brand.lower() \
                    and word.lower() not in (ci.category or "").lower():
                flags.append(f"mentions competitor term '{word}': remove (trademark / comparative-ad risk)")
    from ..respond import numbers_in
    facts, _ = usable_facts(ci)
    allowed = numbers_in(f"{s.name} {s.variant} {s.description} {s.price:g} {s.price:.2f} " + " ".join(f["value"] for f in facts))
    for num in set(re.findall(r"\d+(?:[.,]\d+)?%?", no_urls)):
        if num.rstrip("%") not in allowed:
            flags.append(f"number '{num}' is not in the product data or verified competitor facts: verify or remove")
    return sorted(set(flags))
