"""Triage every new mention and file it under a narrative.

Claude does the reading when a key is set. Without one, a keyword fallback
keeps the dashboard useful (and says so on screen).
"""
from __future__ import annotations

import asyncio

import re

from .. import llm
from .models import Mention, Narrative, Watch

BATCH = 20
GEMINI_BATCH = 40   # free tier allows few requests per minute, so send fewer, larger batches
THREAT_TYPES = ["rumor", "safety", "product_issue", "pricing", "boycott", "legal", "pr_crisis",
                "service_outage", "competitor", "praise", "general"]

SYSTEM = """You are a brand-risk analyst on a social listening desk. You read public posts about a brand and triage them.
For each numbered mention decide:
- relevant: is it actually about this brand (not a namesake, not spam)?
- sentiment: negative | neutral | positive | mixed
- threat_type: one of rumor, safety, product_issue, pricing, boycott, legal, pr_crisis, service_outage, competitor, praise, general
  (rumor = an unverified or false factual claim about the brand spreading; safety = health/injury/contamination; pr_crisis = executive/ad/statement backlash)
- severity: 0 none, 1 low (isolated complaint), 2 elevated (claim others could repeat, or many people affected), 3 high (safety, legal, viral accusation, mainstream outlet carrying a damaging claim)
- is_claim: does it assert something factual about the brand that could be true or false?
- stance toward the narrative it belongs to: spreading (repeats/amplifies it), correcting (debunks/clarifies), neutral (reports)
- summary: one plain sentence, max 20 words, what this mention says
- narrative: the id of an EXISTING narrative it belongs to, or "new: <short title, max 7 words>" for a new storyline.
  Group by storyline (the same underlying claim or event), not by sentiment. Routine chatter, product talk and praise go to broad narratives like "new: General product chatter" or "new: Positive customer reviews".
For each NEW narrative also give claim: the core statement as people repeat it (one sentence), and summary (one sentence).
Never invent facts. Use only what the mention text shows.
Return JSON: {"mentions": [{"i": 1, "relevant": true, "sentiment": "...", "threat_type": "...", "severity": 0, "is_claim": false, "stance": "...", "summary": "...", "narrative": "..."}],
"new_narratives": [{"title": "...", "claim": "...", "summary": "..."}]}"""


def _format(w: Watch, batch: list[Mention]) -> str:
    known = "\n".join(f"- {n.id}: {n.title} ({n.threat_type}) {n.summary[:140]}" for n in w.narratives[:40]) or "(none yet)"
    rows = []
    for i, m in enumerate(batch, 1):
        body = (m.title + " | " if m.title else "") + (m.text or "")
        rows.append(f"[{i}] {m.platform} | {m.author or 'unknown'} | {m.published_at[:10] or 'undated'}\n{body[:700]}")
    product = f" (product/campaign: {w.input.product})" if w.input.product else ""
    return f"Brand: {w.input.brand}{product}\n\nExisting narratives:\n{known}\n\nMentions:\n" + "\n\n".join(rows)


async def triage(w: Watch, new: list[Mention], on_batch=None) -> list[Narrative]:
    """Triage `new` in place. Returns narratives created in this call.
    The first batch runs alone so it seeds the narrative list; the rest run in parallel."""
    created: list[Narrative] = []
    if not llm.available():
        w.triage_mode = "keyword fallback"
        for m in new:
            created += _heuristic(w, m)
        return created
    w.triage_mode = llm.label()
    size = GEMINI_BATCH if llm.provider() == "gemini" and int(__import__("os").environ.get("CONTAGION_GEMINI_CONCURRENCY", "2")) <= 2 else BATCH
    batches = [new[i:i + size] for i in range(0, len(new), size)]
    if not batches:
        return created
    created += await _one_batch(w, batches[0])
    if on_batch:
        on_batch()
    import os
    free_gemini = llm.provider() == "gemini" and int(os.environ.get("CONTAGION_GEMINI_CONCURRENCY", "2")) <= 2
    if len(batches) > 1 and free_gemini:
        for b in batches[1:]:          # sequential on the free tier
            created += await _one_batch(w, b)
            if on_batch:
                on_batch()
    elif len(batches) > 1:
        import asyncio
        for res in await asyncio.gather(*[_one_batch(w, b) for b in batches[1:]]):
            created += res
        if on_batch:
            on_batch()
    return created


async def _one_batch(w: Watch, batch: list[Mention]) -> list[Narrative]:
    created: list[Narrative] = []
    try:
        with llm.fast():   # high-volume labeling runs on the fast model
            data = await asyncio.wait_for(llm.complete_json(SYSTEM, _format(w, batch), max_tokens=6000),
                                      timeout=float(__import__("os").environ.get("CONTAGION_TRIAGE_TIMEOUT", "90")))
    except asyncio.TimeoutError:
        w.triage_errors = (w.triage_errors + [f"{llm.label()} took over 90 s on a batch; keyword fallback used for it"])[-5:]
        w.triage_mode = f"keyword fallback ({llm.label()} too slow)"
        for m in batch:
            created += _heuristic(w, m)
        return created
    except Exception as exc:  # noqa: BLE001  (one bad batch must not stop the watch)
        w.triage_errors = (w.triage_errors + [f"{llm.label()} triage failed, keyword fallback used: {str(exc)[:200]}"])[-5:]
        w.triage_mode = f"keyword fallback ({llm.label()} unavailable)"
        for m in batch:
            created += _heuristic(w, m)
        return created
    titles: dict[str, Narrative] = {}
    for nn in data.get("new_narratives") or []:
        t = str(nn.get("title", "")).strip()[:80]
        if t:
            titles[t.lower()] = Narrative(title=t, claim=str(nn.get("claim", ""))[:300], summary=str(nn.get("summary", ""))[:300])
    by_i = {}
    for r in data.get("mentions") or []:
        try:
            by_i[int(r.get("i", 0))] = r
        except (TypeError, ValueError):
            continue
    for i, m in enumerate(batch, 1):
        r = by_i.get(i)
        if not r:
            created += _heuristic(w, m)
            continue
        m.triaged = True
        m.labeled_by = llm.label()
        m.relevant = bool(r.get("relevant", True))
        m.sentiment = r.get("sentiment") if r.get("sentiment") in ("negative", "neutral", "positive", "mixed") else "neutral"
        m.threat_type = r.get("threat_type") if r.get("threat_type") in THREAT_TYPES else "general"
        try:
            m.severity = max(0, min(3, int(r.get("severity") or 0)))
        except (TypeError, ValueError):
            m.severity = 0
        m.is_claim = bool(r.get("is_claim"))
        m.stance = r.get("stance") if r.get("stance") in ("spreading", "correcting", "neutral") else "neutral"
        m.summary = str(r.get("summary", ""))[:240]
        if not m.relevant:
            continue
        ref = str(r.get("narrative", "")).strip()
        n = next((x for x in w.narratives if x.id == ref), None)
        if n is None:
            title = re.sub(r"^new:\s*", "", ref, flags=re.I).strip()[:80] or _fallback_title(m)
            n = next((x for x in w.narratives if x.title.lower() == title.lower()), None)
            if n is None:
                n = titles.get(title.lower()) or Narrative(title=title, summary=m.summary)
                w.narratives.append(n)
                created.append(n)
        _attach(n, m)
    return created


def _attach(n: Narrative, m: Mention) -> None:
    m.narrative_id = n.id
    if m.id not in n.mention_ids:
        n.mention_ids.append(m.id)
    # A narrative takes the most serious type/severity its members show.
    if m.severity > n.severity or (n.threat_type in ("general", "praise") and m.threat_type not in ("general",)):
        if m.severity >= n.severity:
            n.threat_type = m.threat_type
    n.severity = max(n.severity, m.severity)
    if not n.claim and m.is_claim and m.summary:
        n.claim = m.summary


# ---------- keyword fallback ----------

KEYWORDS = [
    ("safety", 3, "Safety and health claims", r"\b(toxic|poison(ed|ing)?|contaminat\w*|recall(ed|s)?|carcinogen\w*|hospitali[sz]ed|food poisoning|health risk)\b"),
    ("legal", 2, "Lawsuits and legal action", r"\b(lawsuit|class action|sued|sues|suing|settlement over|ftc|attorney general)\b"),
    ("boycott", 2, "Boycott calls", r"\b(boycott\w*|never buying|stop buying)\b"),
    ("pricing", 1, "Pricing backlash", r"\b(price hike|surge pricing|dynamic pricing|overpriced|shrinkflation)\b"),
    ("service_outage", 1, "Outage and service complaints", r"\b(outage|down again|not working)\b"),
    ("pr_crisis", 2, "Backlash and controversy", r"\b(backlash|outrage|scandal|tone[- ]deaf|apologi[sz]es? for)\b"),
    ("product_issue", 1, "Product quality complaints", r"\b(defective|faulty|refund|worst purchase)\b"),
    ("praise", 0, "Positive customer talk", r"\b(love|obsessed|recommend|favorite)\b"),
]
NEG = re.compile(r"\b(bad|worst|hate|awful|terrible|scam|fraud|angry|disgust\w*|never again|lawsuit|recall\w*|boycott\w*|toxic|lead)\b", re.I)
POS = re.compile(r"\b(love|best|amazing|great|awesome|recommend|favorite|excellent)\b", re.I)
CORRECT = re.compile(r"\b(debunk\w*|false|fact[- ]check\w*|not true|denies|denied|clarif\w*|misleading|no evidence)\b", re.I)


def _heuristic(w: Watch, m: Mention) -> list[Narrative]:
    text = f"{m.title} {m.text}".lower()
    brand = w.input.brand.lower().split()[0] if w.input.brand else ""
    m.triaged = True
    m.labeled_by = "keywords"
    full = w.input.brand.lower()
    m.relevant = not brand or full in text or full.replace(" ", "") in text or full.replace(" ", "") in m.url.lower()
    if not m.relevant:
        return []
    m.threat_type, m.severity, title = "general", 0, "General brand chatter"
    for ttype, sev, ntitle, pat in KEYWORDS:
        if re.search(pat, text):
            m.threat_type, m.severity, title = ttype, sev, ntitle
            break
    neg, pos = bool(NEG.search(text)), bool(POS.search(text))
    m.sentiment = "mixed" if neg and pos else "negative" if neg else "positive" if pos else "neutral"
    if m.sentiment == "negative" and m.severity == 0:
        m.severity = 1
    m.stance = "correcting" if CORRECT.search(text) else ("spreading" if m.severity >= 2 else "neutral")
    m.is_claim = m.threat_type in ("rumor", "safety", "legal", "pricing")
    m.summary = (m.title or m.text)[:160]
    created = []
    n = next((x for x in w.narratives if x.title == title), None)
    if n is None:
        n = Narrative(title=title, summary=f"Mentions matching {m.threat_type.replace('_', ' ')} keywords.")
        w.narratives.append(n)
        created.append(n)
    _attach(n, m)
    n.claim = ""   # keyword matches can't tell us the actual claim; don't put a headline in its place
    return created


def _fallback_title(m: Mention) -> str:
    return (m.summary or m.title or "Uncategorized mentions")[:60]
