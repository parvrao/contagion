"""Spread analysis (pure functions, no network): timeline, platform hops,
link edges, correction lag, and the cross-reference between AI citations and
the traced pages."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import Item, Probe
from .sources.base import domain_of

SOCIAL_HOSTS = {"reddit.com", "news.ycombinator.com", "bsky.app", "x.com", "twitter.com", "tiktok.com",
                "instagram.com", "facebook.com", "youtube.com", "threads.net", "threads.com", "news.google.com"}
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "s"}


def norm_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.").replace("old.reddit.com", "reddit.com")
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING])
    return urlunparse(("https", host, p.path.rstrip("/"), "", query, ""))


def _dt(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def relevant(items: list[Item]) -> list[Item]:
    return [i for i in items if i.stance in {"amplifies", "debunks", "reports"}]


def analyze_spread(items: list[Item]) -> dict:
    rel = relevant(items)
    dated = sorted([i for i in rel if _dt(i.published_at)], key=lambda i: _dt(i.published_at))
    amps_all = [i for i in dated if i.stance == "amplifies"]
    # "Earliest" claims only use dates a platform or search index gave us, never model-reported ones.
    amps = [i for i in amps_all if i.date_source != "model-reported"] or amps_all
    debunks = [i for i in dated if i.stance == "debunks"]

    # Platform hops: the order in which the rumor showed up on each platform.
    first_by_platform: dict[str, Item] = {}
    for it in dated:
        if it.stance == "debunks":
            continue
        first_by_platform.setdefault(it.platform, it)
    hops, prev = [], None
    for platform, it in sorted(first_by_platform.items(), key=lambda kv: _dt(kv[1].published_at)):
        t = _dt(it.published_at)
        hops.append({
            "platform": platform, "item_id": it.id, "at": it.published_at,
            "lag_hours": round((t - prev).total_seconds() / 3600, 1) if prev else 0.0,
        })
        prev = t

    # Per platform table.
    per_platform = defaultdict(lambda: {"amplifies": 0, "debunks": 0, "reports": 0, "engagement": 0, "first_seen": ""})
    for it in rel:
        row = per_platform[it.platform]
        row[it.stance] += 1
        if it.stance == "amplifies":
            row["engagement"] += it.engagement
        if it.published_at and (not row["first_seen"] or it.published_at < row["first_seen"]):
            row["first_seen"] = it.published_at

    # Daily series for the timeline chart.
    daily = defaultdict(lambda: Counter())
    for it in dated:
        daily[it.published_at[:10]][it.stance] += 1
    series = [{"day": d, **{k: c.get(k, 0) for k in ("amplifies", "debunks", "reports")}} for d, c in sorted(daily.items())]
    peak = max(series, key=lambda r: r["amplifies"] + r["reports"], default=None)

    # Link edges: one traced page linking to another (real provenance, not guessed).
    by_norm = {norm_url(i.url): i for i in rel}
    edges = []
    for it in rel:
        for link in it.outbound_links:
            target = by_norm.get(norm_url(link))
            if target and target.id != it.id:
                edges.append({"from": target.id, "to": it.id, "kind": "linked"})

    correction_lag = None
    if amps and debunks:
        lag = (_dt(debunks[0].published_at) - _dt(amps[0].published_at)).total_seconds() / 3600
        correction_lag = round(lag, 1)

    return {
        "total_relevant": len(rel),
        "amplifiers": sum(1 for i in rel if i.stance == "amplifies"),
        "debunkers": sum(1 for i in rel if i.stance == "debunks"),
        "reporters": sum(1 for i in rel if i.stance == "reports"),
        "undated": len(rel) - len(dated),
        "earliest_item_id": dated[0].id if dated else None,
        "earliest_amplifier_id": amps[0].id if amps else None,
        "first_debunk_id": debunks[0].id if debunks else None,
        "correction_lag_hours": correction_lag,
        "hops": hops,
        "platforms": dict(per_platform),
        "series": series,
        "peak_day": peak["day"] if peak else None,
        "edges": edges,
        "amplifier_reach": sum(i.engagement for i in rel if i.stance == "amplifies"),
    }


def cross_reference(probes: list[Probe], items: list[Item]) -> dict:
    """Link each AI answer's citations to traced pages. Returns contamination summary."""
    rel = relevant(items)
    by_norm = {norm_url(i.url): i for i in rel}
    news_by_domain = defaultdict(list)
    for i in rel:
        d = i.domain or domain_of(i.url)
        if d and d not in SOCIAL_HOSTS:
            news_by_domain[d].append(i)

    cited_pages: dict[str, dict] = {}
    for p in probes:
        ids = []
        for c in p.citations:
            url = c.get("url", "")
            hit = by_norm.get(norm_url(url))
            if not hit:
                d = domain_of(url)
                if d in SOCIAL_HOSTS or "vertexaisearch" in d:
                    d = (c.get("title") or "").lower().removeprefix("www.")
                # Publisher-level match only counts if that publisher carried the rumor;
                # a cited fact check from the same outlet must not count as contamination.
                candidates = [x for x in news_by_domain.get(d, []) if x.stance == "amplifies"]
                hit = candidates[0] if candidates else None
            if hit:
                ids.append(hit.id)
            if p.verdict == "repeats" and p.confirmed:
                key = norm_url(url)
                row = cited_pages.setdefault(key, {"url": url, "title": c.get("title", ""), "engines": set(), "item_id": hit.id if hit else None})
                row["engines"].add(f"{p.engine} ({p.mode})")
        p.cited_item_ids = sorted(set(ids))

    pages = [dict(v, engines=sorted(v["engines"])) for v in cited_pages.values()]
    pages.sort(key=lambda r: -len(r["engines"]))
    asked = [p for p in probes if p.verdict != "error"]
    repeats = [p for p in asked if p.verdict == "repeats" and p.confirmed]
    return {
        "answers": len(asked),
        "repeats": len(repeats),
        "repeats_unconfirmed": sum(1 for p in asked if p.verdict == "repeats" and not p.confirmed),
        "corrects": sum(1 for p in asked if p.verdict == "corrects"),
        "unaware": sum(1 for p in asked if p.verdict == "unaware"),
        "engines_repeating": sorted({f"{p.engine} ({p.mode})" for p in repeats}),
        "pages_feeding_ai": pages[:25],
        "traced_pages_cited": len({i for p in probes for i in p.cited_item_ids}),
    }


def grade(spread: dict, contamination: dict, items: list[Item]) -> dict:
    amps = [i for i in items if i.stance == "amplifies"]
    platforms_amp = {i.platform for i in amps}
    mainstream = sum(1 for i in amps if i.platform == "News")  # news outlets only, not blogs
    repeat_share = (contamination["repeats"] / contamination["answers"]) if contamination.get("answers") else 0.0

    if contamination.get("repeats"):
        level, label = 4, "Contaminated"
        n = len(contamination["engines_repeating"])
        why = f"{n} AI engine{'s' if n != 1 else ''} repeat the rumor as fact"
    elif mainstream or len(platforms_amp) >= 3:
        level, label = 3, "Mainstream"
        why = f"carried by {mainstream} news outlet(s) across {len(platforms_amp)} platform(s)"
    elif len(platforms_amp) >= 2:
        level, label = 2, "Spreading"
        why = f"amplified on {len(platforms_amp)} platforms"
    elif amps:
        level, label = 1, "Contained"
        why = f"amplified on {next(iter(platforms_amp))} only"
    else:
        level, label = 0, "Not found"
        why = "no public instance spreading the rumor was found"

    parts = {
        "spread": round(min(1.0, math.log10(1 + len(amps)) / math.log10(51)) * 30, 1),
        "platforms": round(min(1.0, len(platforms_amp) / 5) * 15, 1),
        "mainstream": round(min(1.0, mainstream / 5) * 15, 1),
        "ai_contamination": round(repeat_share * 40, 1),
    }
    return {
        "level": level,
        "label": label,
        "why": why,
        "risk_score": round(sum(parts.values())),
        "score_parts": parts,
        "repeat_share": round(repeat_share, 3),
    }
