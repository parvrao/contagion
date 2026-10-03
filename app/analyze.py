"""LLM judgment steps: query planning, stance classification, skeptic pass,
AI-answer verdicts. Each step has a deterministic fallback so the pipeline
still runs (with lower quality) if the model is unavailable or returns junk."""
from __future__ import annotations

import asyncio
import re

from . import llm
from .models import CaseInput, Item, Probe

BATCH = 20


# ---------- 1. Search plan ----------

async def plan_queries(ci: CaseInput) -> tuple[list[str], list[str]]:
    """Return (search_queries, neutral_questions_for_ai_engines)."""
    fallback_q = _fallback_queries(ci)
    fallback_questions = [
        f"Is it true that {ci.claim.rstrip('.?')}?",
        f"What do we know about {ci.brand} and this: {ci.claim.rstrip('.?')}?",
    ]
    if not llm.available():
        return fallback_q, fallback_questions
    try:
        data = await llm.complete_json(
            "You plan research on how a rumor about a brand spread online.",
            f"Brand: {ci.brand}\nRumor: {ci.claim}\nExtra phrases from the analyst: {', '.join(ci.keywords) or 'none'}\n\n"
            "Return {\"queries\": [...], \"questions\": [...]}.\n"
            "queries: 6 short keyword searches (2 to 6 words) the way ordinary people would post or search this rumor, "
            "including slang or hashtags if likely. Always include the brand name.\n"
            "questions: 3 questions a normal consumer might ask an AI assistant about this topic. "
            "Neutral wording: do not assert the rumor is true or false. One should be a direct yes/no check.",
            max_tokens=600,
        )
        queries = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        questions = [q for q in data.get("questions", []) if isinstance(q, str) and q.strip()]
        queries = _unique(ci.keywords + queries)[:8] or fallback_q
        return queries, (questions[:3] or fallback_questions)
    except Exception:
        return fallback_q, fallback_questions


def _fallback_queries(ci: CaseInput) -> list[str]:
    stop = {"the", "a", "an", "is", "are", "to", "of", "and", "in", "on", "for", "will", "that", "it", "its", "be", "with"}
    words = [w for w in re.findall(r"[A-Za-z0-9']+", ci.claim.lower()) if w not in stop and w not in ci.brand.lower()]
    core = " ".join(words[:4])
    return _unique(ci.keywords + [f"{ci.brand} {core}", f"{ci.brand} {' '.join(words[:2])}"])


def _unique(values: list[str]) -> list[str]:
    seen, out = set(), []
    for v in values:
        k = v.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(v.strip())
    return out


# ---------- 2. Stance classification ----------

STANCE_SYSTEM = (
    "You label public posts and articles about a rumor concerning a brand. Labels:\n"
    "amplifies = presents the rumor as true or likely true, or spreads it without correction (including outrage, jokes that assume it is true).\n"
    "debunks = says the rumor is false/misleading, or carries the brand's correction.\n"
    "reports = neutral coverage that mentions both sides or only reports that people are saying it.\n"
    "unrelated = not about this rumor.\n"
    "If the text is too thin to tell, use reports. Judge only from the text given."
)


async def classify(items: list[Item], ci: CaseInput) -> None:
    if not items:
        return
    if not llm.available():
        for it in items:
            it.stance, it.stance_reason = _keyword_stance(it, ci)
        return
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    await asyncio.gather(*[_classify_batch(b, ci) for b in batches])


async def _classify_batch(batch: list[Item], ci: CaseInput) -> None:
    rows = "\n".join(
        f"[{i}] ({it.platform}) {it.title} :: {it.text[:400]}".replace("\n", " ") for i, it in enumerate(batch)
    )
    try:
        with llm.fast():
            data = await llm.complete_json(
            STANCE_SYSTEM,
            f"Brand: {ci.brand}\nRumor: {ci.claim}\nBrand's position: {ci.truth}\n\nItems:\n{rows}\n\n"
            'Return [{"i": index, "stance": label, "reason": "max 15 words"}] for every item.',
            max_tokens=2500,
        )
        by_i = {int(r["i"]): r for r in data if isinstance(r, dict) and "i" in r}
    except Exception:
        by_i = {}
    for i, it in enumerate(batch):
        r = by_i.get(i)
        if r and r.get("stance") in {"amplifies", "debunks", "reports", "unrelated"}:
            it.stance, it.stance_reason = r["stance"], str(r.get("reason", ""))[:160]
        else:
            it.stance, it.stance_reason = _keyword_stance(it, ci)


def _keyword_stance(it: Item, ci: CaseInput) -> tuple[str, str]:
    text = f"{it.title} {it.text}".lower()
    if any(w in text for w in ("false", "debunk", "denies", "not true", "clarif", "won't", "will not", "fact check", "no plans")):
        return "debunks", "keyword match (no model)"
    if ci.brand.lower() in text:
        return "reports", "keyword match (no model)"
    return "unrelated", "brand not mentioned (no model)"


# ---------- 3. Skeptic pass ----------

async def skeptic(items: list[Item], ci: CaseInput) -> int:
    """Second reviewer re-checks every 'amplifies' label. Overturned items drop to 'reports'.
    Returns number overturned."""
    targets = [it for it in items if it.stance == "amplifies"]
    if not targets or not llm.available():
        for it in targets:
            it.confirmed = True
        return 0
    overturned = 0
    import asyncio

    async def review(batch):
        rows = "\n".join(f"[{i}] {it.title} :: {it.text[:400]}".replace("\n", " ") for i, it in enumerate(batch))
        try:
            data = await llm.complete_json(
                "You are a skeptical second reviewer. Another analyst said each item below SPREADS a rumor as if true. "
                "Overturn the label if the item actually corrects it, only reports that others say it, or is too vague to tell. "
                "False accusations against real people are worse than missed items.",
                f"Rumor: {ci.claim}\nBrand's position: {ci.truth}\n\n{rows}\n\n"
                'Return [{"i": index, "confirmed": true|false, "reason": "max 12 words"}].',
                max_tokens=1500,
            )
            verdicts = {int(r["i"]): r for r in data if isinstance(r, dict) and "i" in r}
        except Exception:
            verdicts = {}
        for i, it in enumerate(batch):
            v = verdicts.get(i)
            if v is None:
                # Fail closed: keep the label for analysis, but never flag an
                # account on an unreviewed judgment.
                it.confirmed = False
                it.stance_reason = (it.stance_reason + " (skeptic review unavailable)")[:160]
            elif v.get("confirmed") is True:
                it.confirmed = True
            else:
                it.stance = "reports"
                it.stance_reason = f"overturned by skeptic: {v.get('reason', '')}"[:160]
                flips.append(it)
    flips: list = []
    batches = [targets[k:k + BATCH] for k in range(0, len(targets), BATCH)]
    await asyncio.gather(*[review(b) for b in batches])
    overturned = len(flips)
    return overturned


# ---------- 4. AI answer verdicts ----------

async def judge_probe(p: Probe, ci: CaseInput) -> None:
    if p.error:
        p.verdict = "error"
        return
    if not llm.available():
        p.verdict, p.verdict_reason = "unaware", "no model available to judge"
        return
    try:
        with llm.fast():
            data = await llm.complete_json(
            "You audit an AI assistant's answer for whether it repeats a rumor about a brand.\n"
            "repeats = states or implies the rumor is true, or presents it without the correction.\n"
            "corrects = mentions the rumor and gives the brand's correction or says it is false/misleading.\n"
            "unaware = does not mention the rumor at all.",
            f"Rumor: {ci.claim}\nWhat is actually true: {ci.truth}\n\nQuestion asked: {p.question}\n\nAnswer:\n{p.answer[:3000]}\n\n"
            'Return {"verdict": "repeats|corrects|unaware", "reason": "max 20 words", "quote": "the exact sentence that decided it, or empty"}',
            max_tokens=400,
        )
        verdict = data.get("verdict")
        p.verdict = verdict if verdict in {"repeats", "corrects", "unaware"} else "unaware"
        reason = str(data.get("reason", ""))
        quote = str(data.get("quote", "")).strip()
        p.verdict_reason = (f"{reason} | \"{quote}\"" if quote else reason)[:400]
    except Exception as exc:
        p.verdict, p.verdict_reason = "unaware", f"judge failed: {exc}"[:200]
        return
    if p.verdict == "repeats":
        await _confirm_repeat(p, ci)


async def _confirm_repeat(p: Probe, ci: CaseInput) -> None:
    """Grade 4 hangs on these verdicts, so a second reviewer must agree.
    If the reviewer is unavailable the verdict stays but is marked unconfirmed
    and does not raise the grade on its own."""
    try:
        data = await llm.complete_json(
            "You are a skeptical second reviewer. A first reviewer said this AI answer REPEATS a rumor as if true. "
            "Disagree if the answer corrects the rumor, hedges clearly, or only says some people claim it.",
            f"Rumor: {ci.claim}\nWhat is true: {ci.truth}\n\nAnswer:\n{p.answer[:3000]}\n\n"
            'Return {"confirmed": true|false, "reason": "max 15 words"}',
            max_tokens=200,
        )
    except Exception:
        p.confirmed = False
        p.verdict_reason += " (second review unavailable)"
        return
    if data.get("confirmed") is True:
        p.confirmed = True
    else:
        p.verdict = "corrects" if "correct" in str(data.get("reason", "")).lower() else "unaware"
        p.verdict_reason = f"overturned by skeptic: {data.get('reason', '')}"[:300]
