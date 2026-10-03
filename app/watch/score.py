"""Narrative metrics, threat score and alert rules (pure functions, unit-tested)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .models import Alert, Mention, Narrative, Watch

NEWS_PLATFORMS = {"News"}


def ts(m: Mention) -> datetime:
    for v in (m.published_at, m.found_at):
        if v:
            try:
                d = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def recompute(w: Watch, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    by_id = {m.id: m for m in w.mentions}
    for n in w.narratives:
        ms = [by_id[i] for i in n.mention_ids if i in by_id and by_id[i].relevant]
        n.count = len(ms)
        if not ms:
            n.score, n.status = 0, "fading"
            continue
        times = sorted(ts(m) for m in ms)
        n.first_seen, n.last_seen = times[0].isoformat(timespec="seconds"), times[-1].isoformat(timespec="seconds")
        n.platforms = sorted({m.platform for m in ms})
        n.news_outlets = sorted({m.author or m.domain for m in ms if m.platform in NEWS_PLATFORMS and (m.author or m.domain)})
        n.spreading = sum(m.stance == "spreading" for m in ms)
        n.correcting = sum(m.stance == "correcting" for m in ms)
        n.engagement = sum(m.engagement for m in ms)
        n.negative_share = round(sum(m.sentiment in ("negative", "mixed") for m in ms) / len(ms), 2)
        n.severity = max(m.severity for m in ms)
        n.spark = [0] * 24
        n.spark_days = [0] * 7
        last6 = prior = 0
        for t in times:
            age_h = (now - t).total_seconds() / 3600
            if 0 <= age_h < 24:
                n.spark[23 - int(age_h)] += 1
            if 0 <= age_h < 24 * 7:
                n.spark_days[6 - int(age_h // 24)] += 1
            if age_h < 6:
                last6 += 1
            elif age_h < 24 * 7:
                prior += 1
        n.count_24h = sum(n.spark)
        prior_avg = prior / ((24 * 7 - 6) / 6)
        n.velocity = round(last6 / max(prior_avg, 0.5), 1) if last6 else 0.0
        n.score, n.score_parts = score(n)
        hours_since_last = (now - times[-1]).total_seconds() / 3600
        hours_since_first = (now - times[0]).total_seconds() / 3600
        if hours_since_last > 48:
            n.status = "fading"
        elif n.velocity >= 2 and last6 >= 2:
            n.status = "escalating"
        elif hours_since_first <= 48:
            n.status = "emerging"
        else:
            n.status = "active"
    w.narratives.sort(key=lambda x: (-x.score, -x.count))


def score(n: Narrative) -> tuple[int, dict]:
    parts = {
        "severity": round(n.severity / 3 * 35),
        "volume": round(min(math.log2(n.count + 1) / 6, 1) * 15),
        "velocity": round(min(n.velocity, 5) / 5 * 15) if n.count_24h >= 2 else 0,
        "reach": round(min(len(n.platforms), 4) / 4 * 10) + (8 if n.news_outlets and n.severity >= 2 else 0),
        "negativity": round(n.negative_share * 10),
        "unanswered": round(n.spreading / (n.spreading + n.correcting + 1) * 7),
    }
    total = sum(parts.values())
    if n.threat_type == "praise" or n.severity == 0:
        total = round(total * 0.3)
    return min(100, total), parts


def level_for(score_: int) -> str:
    return "escalate" if score_ >= 70 else "respond" if score_ >= 45 else "prepare" if score_ >= 25 else "monitor"


def snapshot(w: Watch) -> dict:
    return {n.id: {"score": n.score, "platforms": set(n.platforms), "outlets": len(n.news_outlets), "status": n.status}
            for n in w.narratives}


def alerts_for(w: Watch, before: dict, first_cycle: bool) -> list[Alert]:
    out: list[Alert] = []
    for n in w.narratives:
        if n.threat_type == "praise" or (n.severity == 0 and n.score < 25):
            continue
        prev = before.get(n.id)
        tag = f"{n.title}"
        if prev is None:
            if n.severity >= 2:
                lvl = "critical" if n.severity >= 3 or n.score >= 70 else "warn"
                out.append(Alert(narrative_id=n.id, level=lvl, title=f"{'Active' if first_cycle else 'New'} threat: {tag}",
                                 reason=f"{n.count} mentions across {', '.join(n.platforms)}; threat score {n.score}/100."))
            continue
        if prev["score"] < 70 <= n.score:
            out.append(Alert(narrative_id=n.id, level="critical", title=f"Critical: {tag}", reason=f"Threat score rose from {prev['score']} to {n.score}."))
        elif prev["score"] < 45 <= n.score:
            out.append(Alert(narrative_id=n.id, level="warn", title=f"Rising: {tag}", reason=f"Threat score rose from {prev['score']} to {n.score}."))
        new_platforms = set(n.platforms) - prev["platforms"]
        if new_platforms and n.severity >= 1:
            out.append(Alert(narrative_id=n.id, level="warn", title=f"Jumped platforms: {tag}", reason=f"Now on {', '.join(sorted(new_platforms))} (was {', '.join(sorted(prev['platforms'])) or 'none'})."))
        if prev["outlets"] == 0 and n.news_outlets and n.severity >= 2:
            out.append(Alert(narrative_id=n.id, level="warn", title=f"News pickup: {tag}", reason=f"Carried by {', '.join(n.news_outlets[:3])}."))
        if prev["status"] != "escalating" and n.status == "escalating":
            out.append(Alert(narrative_id=n.id, level="warn", title=f"Escalating: {tag}", reason=f"{n.velocity}x the usual mention rate in the last 6 hours."))
    if first_cycle:
        out = sorted(out, key=lambda a: a.level != "critical")[:5]
    return out
