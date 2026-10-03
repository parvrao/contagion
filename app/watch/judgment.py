"""Response judgment: how true is the claim, would replying amplify it, and
where to correct the record. Pure functions (no network), unit-tested.

Principles:
- Most rumors aren't simply true or false. A misframed claim is answered by
  agreeing with the true part and adding context, never with a denial.
- Staying quiet is the default. A public reply is only for rumors that have
  outgrown the quieter fixes, because replying to a small rumor multiplies it.
- Correct the record where people look things up later (AI answers, search),
  then at the source, then with platform tools, and only last in public.
"""
from __future__ import annotations

import re

VERACITY = ("false", "misframed", "true_unflattering", "opinion", "unclear")
VERACITY_LABEL = {
    "false": "False: fabricated",
    "misframed": "Misframed: real fact, wrong conclusion",
    "true_unflattering": "True but unflattering",
    "opinion": "Opinion, not a factual claim",
    "unclear": "Unclear: needs a fact check",
}

FIXED_DONTS = [
    "Don't repeat the rumor's wording.",
    "Don't argue in replies.",
    "Don't deny the parts that are true.",
    "Don't respond before the rumor has reached a big enough audience.",
]

# Channel order: where people check later first, public reply last.
TIER = {
    "internal_brief": 0,
    "faq_update": 1, "ai_answer_page": 1,
    "correction_request": 2,
    "community_note": 3, "support_macro": 3, "task": 3,
    "holding_statement": 4, "social_reply": 4,
}
TIER_LABEL = {0: "Prep", 1: "1. AI answers and search", 2: "2. The original source", 3: "3. Platform tools", 4: "4. Public reply"}
PUBLIC_KINDS = {"holding_statement", "social_reply"}
URGENT_TYPES = {"safety", "legal", "service_outage", "ingredient"}   # health claims are never silenced

DENIAL = re.compile(r"\b(false|untrue|not true|fake news|baseless|never happened|categorically|we deny|denies|deny|no truth|hoax|fabricat\w*)\b", re.I)


def amplification(n, brand_audience: int, ai_answers: int = 0, veracity: str = "unclear") -> dict:
    """Compare the rumor's reach with the reach a brand reply would add.

    rumor_reach is an engagement-based lower bound (likes, comments, points on
    the posts we found), not impressions. brand_audience is what the team
    entered (followers on the main channel); 0 means unknown.
    """
    reach = int(n.engagement or 0)
    platforms = len(n.platforms or [])
    news = len(n.news_outlets or [])
    ratio = round(brand_audience / max(reach, 1), 1) if brand_audience else None
    urgent = n.threat_type in URGENT_TYPES and n.severity >= 2 and veracity in ("true_unflattering", "unclear", "misframed")

    outgrown = (news >= 1 and platforms >= 3) or (brand_audience and reach * 10 >= brand_audience)
    if urgent:
        verdict, why = "act", "Possible safety, legal or outage issue: silence reads as hiding it, so don't wait."
    elif outgrown:
        verdict, why = "public_ok", ("It's in the news on 3+ platforms" if news >= 1 and platforms >= 3
                                     else "Its reach is already at least a tenth of the brand's audience") + ", so a reply won't introduce it to many new people."
    else:
        verdict = "stay_quiet"
        if ratio is not None and ratio >= 10:
            why = f"The brand's audience is about {ratio:g}x the rumor's current reach; a public reply would show it to far more people than have seen it."
        elif ratio is not None:
            why = "It hasn't reached the news or 3+ platforms yet; fix the record quietly first."
        else:
            why = "It hasn't reached the news or 3+ platforms yet; fix the record quietly first. Add the brand's audience size to measure the amplification risk."

    triggers = []
    if news == 0:
        triggers.append("A news outlet picks it up")
    triggers.append(f"It spreads to {platforms + 2} or more platforms (now {platforms})")
    if brand_audience:
        triggers.append(f"Reach passes about {max(brand_audience // 10, 1):,} engagements (now {reach:,})")
    else:
        triggers.append(f"Reach doubles to about {max(reach * 2, 50):,} engagements (now {reach:,})")
    if not ai_answers:
        triggers.append("AI answer engines start repeating it")

    avoided = max(brand_audience - reach, 0) if verdict == "stay_quiet" and brand_audience else 0
    return {
        "verdict": verdict, "why": why, "rumor_reach": reach, "brand_audience": brand_audience,
        "ratio": ratio, "platforms": platforms, "news_outlets": news, "triggers": triggers if verdict == "stay_quiet" else [],
        "exposure_avoided": avoided,
        "reach_note": "Rumor reach = total engagement on the posts found (a lower bound, not impressions).",
    }


def merge_donts(model_donts: list[str]) -> list[str]:
    out = list(FIXED_DONTS)
    seen = {d.lower()[:30] for d in out}
    for d in model_donts or []:
        d = str(d).strip()
        if d and d.lower()[:30] not in seen:
            out.append(d)
            seen.add(d.lower()[:30])
    return out[:6]


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def repeats_wording(draft: str, claim: str, n: int = 5) -> bool:
    """True if the draft reuses any n-word run from the rumor as people state it."""
    c, d = _words(claim), " ".join(_words(draft))
    if len(c) < n:
        return False
    return any(" ".join(c[i:i + n]) in d for i in range(len(c) - n + 1))


def judgment_flags(kind: str, draft: str, claim: str, veracity: str) -> list[str]:
    flags = []
    if not draft or kind == "internal_brief":
        return flags
    if veracity in ("misframed", "true_unflattering") and DENIAL.search(draft):
        flags.append("Denies a claim that is partly true: agree with the true part, then add context")
    if kind in PUBLIC_KINDS | {"faq_update", "ai_answer_page", "community_note"} and repeats_wording(draft, claim):
        flags.append("Repeats the rumor's wording: restate it in your own neutral words")
    return flags


def order_actions(actions: list, verdict: str) -> list:
    """Sort by channel tier; under 'stay quiet', public replies are held, not deleted."""
    for a in actions:
        if verdict == "stay_quiet" and a.kind in PUBLIC_KINDS:
            a.timing = "Hold: publish only if a trigger fires"
            if "Held: stay-quiet recommendation" not in a.flags:
                a.flags = ["Held: stay-quiet recommendation"] + a.flags
    return sorted(actions, key=lambda a: (TIER.get(a.kind, 3), a.kind in PUBLIC_KINDS))


# ---------- SCCT: Situational Crisis Communication Theory (Coombs) ----------
# Crisis clusters by how much responsibility stakeholders attribute to the brand,
# mapped to the response strategy family that fits it. Deny is only for a rumor
# that is actually false; using it anywhere else damages credibility.
SCCT_STRATEGY = {
    "deny": ("Deny", "Only because the claim is false: state the fact plainly, show the source, attack nothing. Never use for partly true claims."),
    "diminish": ("Diminish", "Agree with the true part, add the missing context (justification), and show it is narrower than it sounds."),
    "rebuild": ("Rebuild", "Own it: acknowledge, apologize where warranted, and say what you are doing to fix it (remedy, timeline)."),
    "listen": ("Bolster and listen", "It is an opinion: don't correct it. Acknowledge the experience, fix the underlying issue if there is one, remind people of goodwill."),
    "confirm_first": ("Confirm facts first", "Say nothing substantive until the facts are confirmed; prepare drafts with [CONFIRM] placeholders."),
}


def scct(threat_type: str, veracity: str, severity: int) -> dict:
    if veracity == "false":
        cluster, why = "victim", "A false rumor: the brand is the victim, so responsibility attributed to it is minimal."
        strat = "deny"
    elif veracity == "misframed":
        cluster, why = "accidental", "A real fact with the wrong conclusion attached: low attributed responsibility, a challenge to explain."
        strat = "diminish"
    elif veracity == "true_unflattering":
        if threat_type in ("legal", "pr_crisis", "boycott", "sourcing"):
            cluster, why = "preventable", "True and about the brand's own conduct: stakeholders attribute strong responsibility."
            strat = "rebuild"
        else:
            cluster, why = "accidental", "True, and about a product or service failure rather than misconduct."
            strat = "rebuild" if severity >= 2 or threat_type == "safety" else "diminish"
    elif veracity == "opinion":
        cluster, why = "accidental", "A challenge on opinion grounds; there is no fact to correct."
        strat = "listen"
    else:
        cluster, why = "unclassified", "Not enough verified facts to place it in a cluster yet."
        strat = "confirm_first"
    name, how = SCCT_STRATEGY[strat]
    return {"cluster": cluster, "cluster_why": why, "strategy": strat, "strategy_name": name, "how": how,
            "bolster": strat in ("diminish", "rebuild"),
            "note": "SCCT (Coombs, 2007). Crisis history and prior reputation intensify responsibility; add them in brand facts if relevant."}


# ---------- Stakeholder map (Mendelow power/interest grid) ----------
_STAKEHOLDERS = [
    # name, power, base interest, threat types that raise interest to high
    ("Customers", "low", "high", set()),
    ("Frontline staff and support", "low", "high", set()),
    ("Retail and distribution partners", "high", "low", {"product_issue", "safety", "pricing", "boycott", "service_outage", "ingredient", "formula_change", "availability"}),
    ("Press and journalists", "high", "low", {"safety", "legal", "pr_crisis", "boycott", "ingredient", "sourcing"}),
    ("Investors and board", "high", "low", {"legal", "pr_crisis", "safety"}),
    ("Regulators", "high", "low", {"safety", "legal", "ingredient"}),
]
_QUADRANT = {
    ("high", "high"): ("Manage closely", 0, "Brief directly before anything public; give them the facts and the plan."),
    ("high", "low"): ("Keep satisfied", 1, "Short heads-up if it grows; no need to involve them yet."),
    ("low", "high"): ("Keep informed", 2, "Give them the approved answer so they say the same thing."),
    ("low", "low"): ("Monitor", 3, "No action now."),
}


def stakeholder_map(threat_type: str, severity: int, news_outlets: int, level: str) -> list[dict]:
    out = []
    for name, power, interest, raises in _STAKEHOLDERS:
        hi = interest == "high" or threat_type in raises
        if name == "Press and journalists" and news_outlets:
            hi = True
        if name == "Investors and board" and (severity >= 3 or level == "escalate"):
            hi = True
        if severity <= 1 and name not in ("Customers", "Frontline staff and support"):
            hi = False
        quad, rank, action = _QUADRANT[(power, "high" if hi else "low")]
        out.append({"stakeholder": name, "power": power, "interest": "high" if hi else "low", "quadrant": quad, "rank": rank, "action": action})
    return sorted(out, key=lambda r: r["rank"])


# ---------- RACI per action ----------
_ACCOUNTABLE = {
    "faq_update": "Head of Brand", "ai_answer_page": "Head of Brand",
    "correction_request": "Head of Communications", "community_note": "Head of Communications",
    "holding_statement": "Head of Communications", "social_reply": "Head of Communications",
    "support_macro": "Head of Customer Support", "internal_brief": "Head of Communications", "task": "Exec sponsor",
}


def raci(kind: str, owner: str, level: str, threat_type: str) -> dict:
    external = kind in ("faq_update", "ai_answer_page", "correction_request", "community_note", "holding_statement", "social_reply")
    consulted = []
    if external:
        consulted.append("Legal")
    if threat_type in ("product_issue", "safety", "service_outage", "ingredient", "formula_change", "availability"):
        consulted.append("Product / Ops")
    informed = ["Customer Support"] if kind != "support_macro" else ["Social team"]
    if level in ("respond", "escalate"):
        informed.append("Exec sponsor")
    a = _ACCOUNTABLE.get(kind, "Head of Communications")
    r = owner or a
    return {"R": r, "A": a, "C": consulted, "I": [i for i in informed if i != r]}
