"""Mitigation playbook: a plain-English read of a narrative plus concrete,
owner-assigned actions and drafts. Every action starts pending; a person
approves, edits or rejects it. Nothing is posted or sent by the system."""
from __future__ import annotations

import re

from .. import llm
from .models import Mention, Narrative, Playbook, Watch, WatchAction
from .score import level_for

KINDS = ["holding_statement", "social_reply", "support_macro", "faq_update", "correction_request", "internal_brief", "task"]

SYSTEM = """You are the head of brand communications triaging a live narrative about your brand.
Write for a busy marketing lead: plain English, specific, no jargon, no filler.

You get: the brand, optional verified brand facts, the narrative metrics and the evidence (public posts with URLs).

Rules:
- Use ONLY facts in "Verified brand facts" or visible in the evidence. If a draft needs a fact you do not have, write a placeholder like [CONFIRM: recall status] instead of guessing.
- Do not argue with or name private individuals. Address the claim, not the person.
- Correction requests may only target URLs that appear in the evidence.
- Match the response to the threat: do not recommend a public statement for a low-risk, low-reach narrative (that amplifies it). Say "monitor" when that is right.
- Each action has an owner role (e.g. Social lead, PR lead, Support lead, Legal, Web/SEO, Exec sponsor), a timing (e.g. "within 1 hour", "today", "if score passes 60") and why it matters.

Return JSON:
{"what": "what people are saying, 1 to 2 sentences",
 "who": "who is carrying it (platforms, outlet types, notable public accounts by handle only)",
 "how_fast": "speed and trajectory in one sentence, using the metrics",
 "why_it_matters": "business risk in one sentence",
 "response_level": "monitor | prepare | respond | escalate",
 "level_reason": "one sentence",
 "do_not": ["1 to 3 things to avoid"],
 "watch_for": ["1 to 3 signals that should change the plan"],
 "actions": [{"kind": "holding_statement | social_reply | support_macro | faq_update | correction_request | internal_brief | task",
              "title": "short imperative", "owner": "role", "timing": "...", "channel": "where", "target_url": "evidence URL or empty",
              "why": "one sentence", "draft": "ready-to-edit text, or empty for a plain task"}]}
Give 3 to 6 actions, most urgent first. Always include one internal_brief."""


def _evidence(w: Watch, n: Narrative, k: int = 15) -> list[Mention]:
    by_id = {m.id: m for m in w.mentions}
    ms = [by_id[i] for i in n.mention_ids if i in by_id and by_id[i].relevant]
    ms.sort(key=lambda m: (-m.severity, m.stance != "spreading", -m.engagement))
    return ms[:k]


def _prompt(w: Watch, n: Narrative, ev: list[Mention]) -> str:
    facts = w.input.position.strip() or "(none provided: use placeholders for any brand fact)"
    rows = "\n".join(
        f"- [{m.platform}] {m.author or m.domain} {m.published_at[:10]} stance={m.stance} sev={m.severity} eng={m.engagement} {m.url}\n  {m.summary or (m.title + ' ' + m.text)[:300]}"
        for m in ev)
    return (f"Brand: {w.input.brand}{' / ' + w.input.product if w.input.product else ''}\n"
            f"Verified brand facts: {facts}\n\n"
            f"Narrative: {n.title}\nClaim as people state it: {n.claim or n.summary}\nThreat type: {n.threat_type}, severity {n.severity}/3\n"
            f"Threat score: {n.score}/100 (suggested level: {level_for(n.score)})\n"
            f"Mentions: {n.count} total, {n.count_24h} in 24h, velocity {n.velocity}x baseline, status {n.status}\n"
            f"Platforms: {', '.join(n.platforms)}; news outlets: {', '.join(n.news_outlets) or 'none'}\n"
            f"Spreading {n.spreading} / correcting {n.correcting}; negative share {int(n.negative_share * 100)}%\n"
            f"AI answer engines (Profound): {_ai_line(n)}\n\n"
            f"Evidence:\n{rows}")


def _ai_line(n: Narrative) -> str:
    ex = n.ai_exposure or {}
    if not ex.get("checked"):
        return "not checked"
    if not ex.get("answers"):
        return f"none of {ex['checked']} recent AI answers repeat or cite it"
    return f"{ex['answers']} of {ex['checked']} recent AI answers repeat or cite it on {', '.join(ex.get('models', []))}; prioritize fixing the pages they cite"


async def build(w: Watch, n: Narrative) -> tuple[Playbook, list[WatchAction]]:
    ev = _evidence(w, n)
    allowed_urls = {m.url for m in ev} | ({f"https://{w.input.domain}"} if w.input.domain else set())
    if llm.available():
        try:
            data = await llm.complete_json(SYSTEM, _prompt(w, n, ev), max_tokens=3500)
            pb = Playbook(
                by=llm.label(), what=str(data.get("what", "")), who=str(data.get("who", "")),
                how_fast=str(data.get("how_fast", "")), why_it_matters=str(data.get("why_it_matters", "")),
                response_level=data.get("response_level") if data.get("response_level") in ("monitor", "prepare", "respond", "escalate") else level_for(n.score),
                level_reason=str(data.get("level_reason", "")),
                do_not=[str(x) for x in (data.get("do_not") or [])][:3],
                watch_for=[str(x) for x in (data.get("watch_for") or [])][:3],
            )
            actions = []
            for a in (data.get("actions") or [])[:6]:
                kind = a.get("kind") if a.get("kind") in KINDS else "task"
                target = str(a.get("target_url", ""))
                if target and target not in allowed_urls:
                    target = ""
                act = WatchAction(narrative_id=n.id, kind=kind, title=str(a.get("title", ""))[:160], owner=str(a.get("owner", ""))[:60],
                                  timing=str(a.get("timing", ""))[:60], channel=str(a.get("channel", ""))[:80], target_url=target,
                                  why=str(a.get("why", ""))[:300], draft=str(a.get("draft", ""))[:3000])
                act.flags = guardrail(act.draft, w, allowed_urls)
                actions.append(act)
            if actions:
                return pb, actions
        except Exception:  # noqa: BLE001  (fall back to the template rather than leave the analyst empty-handed)
            pass
    return template(w, n, ev)


def guardrail(text: str, w: Watch, allowed_urls: set[str]) -> list[str]:
    flags = []
    facts = w.input.position or ""
    for url in re.findall(r"https?://[^\s)\]]+", text or ""):
        if url.rstrip(".,") not in allowed_urls and (not w.input.domain or w.input.domain not in url):
            flags.append(f"Link not in evidence or brand domain: {url[:80]}")
    for num in set(re.findall(r"(?<![\w/.-])\d[\d,.]*%?", re.sub(r"https?://\S+", "", text or ""))):
        if num not in facts and len(num.strip("%.,")) > 0 and not num.startswith(("1.", "2.", "3.", "4.", "5.")):
            flags.append(f"Number not in brand facts: {num}")
    if "[CONFIRM" in (text or ""):
        flags.append("Has placeholders to fill before use")
    return flags[:6]


def template(w: Watch, n: Narrative, ev: list[Mention]) -> tuple[Playbook, list[WatchAction]]:
    lvl = level_for(n.score)
    brand = w.input.brand
    top = ev[0] if ev else None
    pb = Playbook(
        by="template",
        what=f"{n.count} public mentions match {n.title.lower()} keywords. Read the evidence to confirm what is being claimed.",
        who=f"Carried on {', '.join(n.platforms) or 'unknown platforms'}" + (f"; news outlets: {', '.join(n.news_outlets[:3])}." if n.news_outlets else "."),
        how_fast=f"{n.count_24h} mentions in the last 24 hours, {n.velocity}x the usual rate; status: {n.status}.",
        why_it_matters="Unanswered claims get repeated by search and AI answer engines long after the news cycle." if n.severity >= 2 else "Low risk today; worth tracking for a change in speed or reach.",
        response_level=lvl, level_reason=f"Threat score {n.score}/100 maps to '{lvl}'.",
        do_not=["Do not reply to every post; it amplifies the claim.", "Do not state facts Legal has not confirmed."],
        watch_for=["A mainstream outlet picks it up", "Velocity passes 3x baseline", "AI answer engines start repeating it"],
    )
    acts = [WatchAction(narrative_id=n.id, kind="internal_brief", title="Brief the comms and support leads", owner="Brand/comms lead", timing="within 1 hour",
                        channel="Slack / email", why="Everyone answers the same way.",
                        draft=f"Heads-up: \"{n.title}\" about {brand} is {n.status} ({n.count} mentions, threat score {n.score}/100) on {', '.join(n.platforms)}.\n"
                              f"What people say: {n.claim or n.summary}\nOur position: [CONFIRM: approved facts]\nAsk: do not engage individually until the holding statement is approved.")]
    if lvl in ("prepare", "respond", "escalate"):
        acts.append(WatchAction(narrative_id=n.id, kind="holding_statement", title="Approve a holding statement", owner="PR lead", timing="today",
                                channel="Website / press", why="Ready before reporters call.",
                                draft=f"We're aware of posts claiming {n.claim or '[CONFIRM: claim]'}. [CONFIRM: what is true]. We'll share updates at [CONFIRM: URL]."))
        acts.append(WatchAction(narrative_id=n.id, kind="support_macro", title="Give support a reply macro", owner="Support lead", timing="today",
                                channel="Support desk", why="Customers ask support first.",
                                draft=f"Thanks for asking. [CONFIRM: what is true about {n.title.lower()}]. You can read our latest update here: [CONFIRM: URL]."))
    if lvl in ("respond", "escalate"):
        acts.append(WatchAction(narrative_id=n.id, kind="faq_update", title="Publish an FAQ answer on the brand site", owner="Web/SEO", timing="within 24 hours",
                                channel="Brand site", why="Gives search and AI engines a source to cite.",
                                draft=f"Q: {n.claim or n.title}?\nA: [CONFIRM: answer].", target_url=f"https://{w.input.domain}" if w.input.domain else ""))
        if top:
            acts.append(WatchAction(narrative_id=n.id, kind="correction_request", title=f"Ask {top.author or top.domain} for a correction", owner="PR lead",
                                    timing="after statement is approved", channel="Email", target_url=top.url, why="Highest-reach page carrying the claim.",
                                    draft=f"Hello, your piece at {top.url} says {n.claim or '[CONFIRM: claim]'}. [CONFIRM: correct facts and source]. Would you consider an update?"))
    if lvl == "escalate":
        acts.append(WatchAction(narrative_id=n.id, kind="task", title="Loop in Legal and the exec sponsor", owner="Exec sponsor", timing="within 2 hours",
                                channel="Call", why="High-severity claims need a decision owner."))
    for a in acts:
        # Internal briefs quote our own metrics, so only external drafts get the number check.
        a.flags = guardrail(a.draft, w, {m.url for m in ev}) if a.kind != "internal_brief" else (["Has placeholders to fill before use"] if "[CONFIRM" in a.draft else [])
    return pb, acts
