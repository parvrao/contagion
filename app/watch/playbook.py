"""Mitigation playbook: a plain-English read of a narrative plus concrete,
owner-assigned actions and drafts. Every action starts pending; a person
approves, edits or rejects it. Nothing is posted or sent by the system."""
from __future__ import annotations

import re

from .. import llm, store
from . import judgment
from .models import Mention, Narrative, Playbook, Watch, WatchAction
from .score import level_for

KINDS = ["holding_statement", "social_reply", "support_macro", "faq_update", "correction_request", "community_note", "internal_brief", "task"]

SYSTEM = """You are the head of brand communications triaging a live narrative about your brand.
Write for a busy marketing lead: plain English, specific, no jargon, no filler.

You get: the brand, optional verified brand facts, the narrative metrics and the evidence (public posts with URLs).

Step 1, before planning anything: decide how true the claim is.
- false: fabricated outright.
- misframed: a real fact with the wrong conclusion attached (the most common type).
- true_unflattering: accurate, just bad for the brand.
- opinion: a take, not a factual claim.
- unclear: you can't tell from the verified facts and evidence (then drafts use [CONFIRM] placeholders).
Record the true part and the missing context. A misframed or true claim is NEVER answered with a denial: agree with the true part and add the context ("Yes, X happened. Here's what it does and doesn't mean."). Opinions are not "corrected"; at most, listen and fix the underlying experience.

Then pick the SCCT (Coombs) response strategy that fits: deny ONLY a false rumor; diminish a misframed claim (agree with the true part, add context); rebuild for a true failure (acknowledge, apologize where warranted, remedy); for opinion, listen and bolster, don't correct; if unclear, confirm facts first. Write every draft in that strategy.

Step 2: respect the amplification check you are given. If it says stay_quiet, the response level is monitor or prepare, and there is no public reply: prepare quietly and list the triggers that would change that.

Step 3: correct the record where people look things up later, in this order:
1. faq_update: a clear page or FAQ on the brand's own site that AI answer engines and search will cite (title it as the question people ask, neutral wording).
2. correction_request: ask the original high-reach source (given below) to correct or update its post.
3. community_note or correction_request to journalists: platform tools.
4. holding_statement / social_reply: public reply, only when the rumor has outgrown everything above.

Rules:
- Never repeat the rumor's wording in public-facing drafts; restate it neutrally.
- Use ONLY facts in "Verified brand facts" or visible in the evidence. If a draft needs a fact you do not have, write a placeholder like [CONFIRM: recall status] instead of guessing.
- Do not argue with or name private individuals. Address the claim, not the person.
- Correction requests may only target URLs that appear in the evidence.
- Match the response to the threat: do not recommend a public statement for a low-risk, low-reach narrative (that amplifies it). Say "monitor" when that is right.
- Each action has an owner role (e.g. Social lead, PR lead, Support lead, Legal, Web/SEO, Exec sponsor), a timing (e.g. "within 1 hour", "today", "if score passes 60") and why it matters.

Return JSON:
{"veracity": "false | misframed | true_unflattering | opinion | unclear",
 "true_part": "what is actually true in it, or empty",
 "missing_context": "what the claim leaves out, or empty",
 "veracity_basis": "which verified fact or evidence decided it, one sentence",
 "what": "what people are saying, 1 to 2 sentences",
 "who": "who is carrying it (platforms, outlet types, notable public accounts by handle only)",
 "how_fast": "speed and trajectory in one sentence, using the metrics",
 "why_it_matters": "business risk in one sentence",
 "response_level": "monitor | prepare | respond | escalate",
 "level_reason": "one sentence",
 "do_not": ["1 to 3 things to avoid"],
 "watch_for": ["1 to 3 signals that should change the plan"],
 "actions": [{"kind": "faq_update | correction_request | community_note | support_macro | holding_statement | social_reply | internal_brief | task",
              "title": "short imperative", "owner": "role", "timing": "...", "channel": "where", "target_url": "evidence URL or empty",
              "why": "one sentence", "draft": "ready-to-edit text, or empty for a plain task"}]}
Give 3 to 6 actions in the channel order above. Always include one internal_brief and, unless the claim is opinion, one faq_update."""


def original_source(w: Watch, n: Narrative, ev: list[Mention]) -> dict:
    """The one high-reach source worth asking for a correction. Deep trace's earliest
    spreading post wins when a trace exists; otherwise the highest-reach spreading post."""
    if n.trace_case_id:
        case = store.get(n.trace_case_id)
        sp = getattr(case, "spread", None) or {}
        first = next((i for i in getattr(case, "items", []) if i.id == sp.get("earliest_amplifier_id")), None)
        if first:
            return {"url": first.url, "author": first.author or first.domain, "platform": first.platform,
                    "why": f"Earliest spreading post found by Deep trace ({first.published_at[:10]})"}
    spreading = [m for m in ev if m.stance == "spreading"] or ev
    if not spreading:
        return {}
    top = max(spreading, key=lambda m: (m.engagement, -len(m.published_at or "z")))
    return {"url": top.url, "author": top.author or top.domain, "platform": top.platform,
            "why": f"Highest-reach post carrying it ({top.engagement:,} engagements)"}


def _evidence(w: Watch, n: Narrative, k: int = 15) -> list[Mention]:
    by_id = {m.id: m for m in w.mentions}
    ms = [by_id[i] for i in n.mention_ids if i in by_id and by_id[i].relevant]
    ms.sort(key=lambda m: (-m.severity, m.stance != "spreading", -m.engagement))
    return ms[:k]


def _prompt(w: Watch, n: Narrative, ev: list[Mention], amp: dict, src: dict) -> str:
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
            f"AI answer engines (Profound): {_ai_line(n)}\n"
            f"Amplification check (computed, follow it): verdict={amp['verdict']}; {amp['why']} Rumor reach {amp['rumor_reach']:,} engagements; brand audience {amp['brand_audience'] or 'unknown'}.\n"
            f"Original high-reach source: {src.get('url') or 'none found'} ({src.get('author', '')}, {src.get('why', '')})\n\n"
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
    src = original_source(w, n, ev)
    allowed_urls = {m.url for m in ev} | ({f"https://{w.input.domain}"} if w.input.domain else set()) | ({src["url"]} if src.get("url") else set())
    ai_answers = (n.ai_exposure or {}).get("answers", 0)
    pb, acts = None, []
    if llm.available():
        try:
            amp0 = judgment.amplification(n, w.input.brand_audience, ai_answers)
            data = await llm.complete_json(SYSTEM, _prompt(w, n, ev, amp0, src), max_tokens=4000)
            pb = Playbook(
                by=llm.label(), what=str(data.get("what", "")), who=str(data.get("who", "")),
                how_fast=str(data.get("how_fast", "")), why_it_matters=str(data.get("why_it_matters", "")),
                response_level=data.get("response_level") if data.get("response_level") in ("monitor", "prepare", "respond", "escalate") else level_for(n.score),
                level_reason=str(data.get("level_reason", "")),
                do_not=[str(x) for x in (data.get("do_not") or [])][:3],
                watch_for=[str(x) for x in (data.get("watch_for") or [])][:3],
            )
            v = data.get("veracity")
            pb.veracity = v if v in judgment.VERACITY else "unclear"
            pb.true_part = str(data.get("true_part", ""))[:400]
            pb.missing_context = str(data.get("missing_context", ""))[:400]
            pb.veracity_basis = str(data.get("veracity_basis", ""))[:300]
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
                acts = actions
            else:
                pb = None
        except Exception:  # noqa: BLE001  (fall back to the template rather than leave the analyst empty-handed)
            pb = None
    if pb is None:
        pb, acts = template(w, n, ev, src)
    return finalize(w, n, pb, acts, src, ai_answers)


def finalize(w: Watch, n: Narrative, pb: Playbook, acts: list[WatchAction], src: dict, ai_answers: int) -> tuple[Playbook, list[WatchAction]]:
    """Apply the judgment layer the same way to model-written and template plans."""
    amp = judgment.amplification(n, w.input.brand_audience, ai_answers, pb.veracity)
    pb.amplification, pb.original_source = amp, src
    if amp["verdict"] == "stay_quiet" and pb.response_level in ("respond", "escalate"):
        pb.level_reason = f"Held at 'prepare' by the amplification check: {amp['why']}"
        pb.response_level = "prepare"
    elif amp["verdict"] == "act" and pb.response_level == "monitor":
        pb.response_level, pb.level_reason = "respond", amp["why"]
    pb.do_not = judgment.merge_donts(pb.do_not)
    pb.scct = judgment.scct(n.threat_type, pb.veracity, n.severity)
    pb.stakeholders = judgment.stakeholder_map(n.threat_type, n.severity, len(n.news_outlets or []), pb.response_level)
    for a in acts:
        a.raci = judgment.raci(a.kind, a.owner, pb.response_level, n.threat_type)
    claim = n.claim or n.title
    for a in acts:
        a.flags = list(dict.fromkeys(a.flags + judgment.judgment_flags(a.kind, a.draft, claim, pb.veracity)))[:6]
    return pb, judgment.order_actions(acts, amp["verdict"])


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


def template(w: Watch, n: Narrative, ev: list[Mention], src: dict | None = None) -> tuple[Playbook, list[WatchAction]]:
    lvl = level_for(n.score)
    brand = w.input.brand
    src = src or {}
    top = next((m for m in ev if m.url == src.get("url")), ev[0] if ev else None)
    pb = Playbook(
        by="template",
        what=f"{n.count} public mentions match {n.title.lower()} keywords. Read the evidence to confirm what is being claimed.",
        who=f"Carried on {', '.join(n.platforms) or 'unknown platforms'}" + (f"; news outlets: {', '.join(n.news_outlets[:3])}." if n.news_outlets else "."),
        how_fast=f"{n.count_24h} mentions in the last 24 hours, {n.velocity}x the usual rate; status: {n.status}.",
        why_it_matters="Unanswered claims get repeated by search and AI answer engines long after the news cycle." if n.severity >= 2 else "Low risk today; worth tracking for a change in speed or reach.",
        response_level=lvl, level_reason=f"Threat score {n.score}/100 maps to '{lvl}'.",
        do_not=["Don't state facts Legal has not confirmed."],
        watch_for=["A mainstream outlet picks it up", "Velocity passes 3x baseline", "AI answer engines start repeating it"],
    )
    acts = [WatchAction(narrative_id=n.id, kind="internal_brief", title="Brief the comms and support leads", owner="Brand/comms lead", timing="within 1 hour",
                        channel="Slack / email", why="Everyone answers the same way.",
                        draft=f"Heads-up: \"{n.title}\" about {brand} is {n.status} ({n.count} mentions, threat score {n.score}/100) on {', '.join(n.platforms)}.\n"
                              f"What people say: {n.claim or n.summary}\nOur position: [CONFIRM: approved facts]\nAsk: do not engage individually until the holding statement is approved.")]
    acts.append(WatchAction(narrative_id=n.id, kind="faq_update", title="Publish the answer AI engines should cite", owner="Web/SEO", timing="within 24 hours",
                            channel="Brand site FAQ", why="People ask ChatGPT and Google before they buy, invest or write; give them a source to cite. Then re-check with Profound.",
                            draft="Q: [CONFIRM: the question people ask, in neutral words]\nA: [CONFIRM: true part]. [CONFIRM: context: what it does and doesn't mean]. Last updated [CONFIRM: date].",
                            target_url=f"https://{w.input.domain}" if w.input.domain else ""))
    if lvl in ("prepare", "respond", "escalate"):
        acts.append(WatchAction(narrative_id=n.id, kind="holding_statement", title="Approve a holding statement", owner="PR lead", timing="today",
                                channel="Website / press", why="Ready before reporters call.",
                                draft="We've seen questions about [CONFIRM: neutral description of the topic]. Here's what's true: [CONFIRM: true part]. What it does and doesn't mean: [CONFIRM: context]. Updates: [CONFIRM: URL]."))
        acts.append(WatchAction(narrative_id=n.id, kind="support_macro", title="Give support a reply macro", owner="Support lead", timing="today",
                                channel="Support desk", why="Customers ask support first.",
                                draft=f"Thanks for asking. [CONFIRM: what is true about {n.title.lower()}]. You can read our latest update here: [CONFIRM: URL]."))
    if top and lvl != "monitor":
        acts.append(WatchAction(narrative_id=n.id, kind="correction_request", title=f"Ask {top.author or top.domain} to update the post", owner="PR lead",
                                timing="today", channel="Email / DM", target_url=top.url, why=src.get("why") or "Highest-reach page carrying the claim.",
                                draft=f"Hello, thanks for covering this. In your post at {top.url}, [CONFIRM: the specific point]. What's accurate: [CONFIRM: true part]. "
                                      "What's missing: [CONFIRM: context and source]. Would you consider an update or note?"))
    if lvl == "escalate":
        acts.append(WatchAction(narrative_id=n.id, kind="task", title="Loop in Legal and the exec sponsor", owner="Exec sponsor", timing="within 2 hours",
                                channel="Call", why="High-severity claims need a decision owner."))
    for a in acts:
        # Internal briefs quote our own metrics, so only external drafts get the number check.
        a.flags = guardrail(a.draft, w, {m.url for m in ev}) if a.kind != "internal_brief" else (["Has placeholders to fill before use"] if "[CONFIRM" in a.draft else [])
    return pb, acts
