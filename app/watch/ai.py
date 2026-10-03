"""Profound: are AI answer engines already repeating a narrative?

1. Match the watched brand to one of the org's Profound categories
   (GET /v1/org/categories), falling back to PROFOUND_CATEGORY_ID.
2. Pull recent raw AI answers for that category (POST /v1/prompts/answers):
   prompt, response text, model, citations.
3. For each threat narrative, find answers that cite a page carrying it, or
   whose text repeats its key terms. Optionally count citations of those
   hostnames per model (POST /v1/reports/citations).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .. import http
from ..config import settings
from .models import Narrative, Watch

_categories: list[dict] = []
STOP = set("about after again against their there these those which while would could should being other first still where there's because people says said claim claims brand video posts post really maybe today since under""".split())


def enabled() -> tuple[bool, str]:
    return (True, "") if settings.profound_api_key else (False, "set PROFOUND_API_KEY to enable")


def _base() -> str:
    return settings.profound_base_url.rstrip("/")


def _headers() -> dict:
    return {"X-API-Key": settings.profound_api_key}


async def categories() -> list[dict]:
    global _categories
    if not _categories:
        data = await http.get_json(f"{_base()}/v1/org/categories", headers=_headers(), timeout=30.0)
        rows = data if isinstance(data, list) else data.get("data", [])
        _categories = [{"id": r.get("id"), "name": r.get("name", "")} for r in rows if r.get("id")]
    return _categories


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


async def match_category(w: Watch) -> tuple[str, str]:
    """Return (category_id, category_name) for this brand, or ("", reason)."""
    brand = _norm(w.input.brand)
    try:
        cats = await categories()
    except Exception as exc:  # noqa: BLE001
        cats = []
        if not settings.profound_category_id:
            return "", f"could not list categories: {str(exc)[:120]}"
    for c in cats:
        name = _norm(c["name"])
        if brand and (brand in name or name.split(" ")[-1] == brand):
            return c["id"], c["name"]
    if settings.profound_category_id:
        name = next((c["name"] for c in cats if c["id"] == settings.profound_category_id), "PROFOUND_CATEGORY_ID")
        return settings.profound_category_id, name
    names = ", ".join(c["name"].split(" - ")[-1] for c in cats[:5])
    return "", f"no Profound category tracks this brand (tracked: {names or 'none'})"


async def answers(category_id: str, days: int = 7) -> list[dict]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    data = await http.post_json(f"{_base()}/v1/prompts/answers",
                                {"category_id": category_id, "start_date": start.isoformat(), "end_date": end.isoformat()},
                                headers=_headers(), timeout=90.0)
    rows = data.get("data", []) if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        cites = r.get("citations") or []
        cites = [c if isinstance(c, str) else (c.get("url") or "") for c in cites]
        out.append({"model": r.get("model", ""), "prompt": r.get("prompt", ""), "response": r.get("response", "") or "",
                    "created_at": r.get("created_at", ""), "citations": [c for c in cites if c]})
    return out


async def citation_counts(category_id: str, hosts: list[str], days: int = 30) -> list[dict]:
    if not hosts:
        return []
    end = datetime.now(timezone.utc)
    data = await http.post_json(f"{_base()}/v1/reports/citations", {
        "category_id": category_id, "start_date": (end - timedelta(days=days)).date().isoformat(), "end_date": end.date().isoformat(),
        "metrics": ["count"], "dimensions": ["hostname", "model"],
        "filters": [{"field": "hostname", "operator": "in", "value": hosts[:50]}],
    }, headers=_headers(), timeout=60.0)
    rows = []
    for row in data.get("data", []):
        d, m = row.get("dimensions", []), row.get("metrics", [])
        rows.append({"hostname": d[0] if d else "", "model": d[1] if len(d) > 1 else "", "count": m[0] if m else 0})
    return rows


def host(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


GENERIC_HOSTS = {"news.google.com", "youtube.com", "bsky.app", "reddit.com", "x.com", "twitter.com", "tiktok.com",
                 "news.ycombinator.com", "facebook.com", "instagram.com", "vertexaisearch.cloud.google.com"}


def keywords(n: Narrative, brand: str) -> list[str]:
    b = set(_norm(brand).split())
    words = re.findall(r"[a-z][a-z'-]{3,}", f"{n.claim} {n.title}".lower())
    out = []
    for wd in words:
        if wd in STOP or wd in b or wd in out:
            continue
        out.append(wd)
    return out[:6]


def match(w: Watch, n: Narrative, rows: list[dict]) -> dict:
    by_id = {m.id: m for m in w.mentions}
    mentions = [by_id[i] for i in n.mention_ids if i in by_id]
    urls = {m.url.split("#")[0].rstrip("/") for m in mentions}
    hosts = {host(m.url) for m in mentions} | {m.domain for m in mentions if m.domain}
    hosts = {h for h in hosts if h and h not in GENERIC_HOSTS}
    kws = keywords(n, w.input.brand)
    need = 2 if len(kws) >= 3 else 1
    hits = []
    for r in rows:
        text = r["response"].lower()
        cited_pages = [c for c in r["citations"] if c.split("#")[0].rstrip("/") in urls]
        cited_hosts = [c for c in r["citations"] if host(c) in hosts]
        terms = [k for k in kws if k in text]
        brand_named = _norm(w.input.brand).split(" ")[0] in _norm(text)
        if cited_pages or cited_hosts or (brand_named and len(terms) >= need):
            idx = min((text.find(t) for t in terms if text.find(t) >= 0), default=0)
            hits.append({"model": r["model"], "prompt": r["prompt"][:200], "created_at": r["created_at"],
                         "snippet": r["response"][max(0, idx - 120): idx + 220].strip(),
                         "why": "cites a page carrying it" if cited_pages else "cites a site carrying it" if cited_hosts else f"repeats: {', '.join(terms)}",
                         "citations": (cited_pages or cited_hosts)[:3]})
    models = sorted({h["model"] for h in hits if h["model"]})
    return {"answers": len(hits), "models": models, "examples": hits[:5], "checked": len(rows),
            "hosts": sorted(hosts)[:20], "keywords": kws}


async def refresh(w: Watch) -> None:
    """Update AI exposure for every threat narrative. Fails soft into w.ai_status."""
    ok, note = enabled()
    if not ok:
        w.ai_status = f"skipped: {note}"
        return
    if not w.ai_category_id:
        cid, name = await match_category(w)
        if not cid:
            w.ai_status = f"skipped: {name}"
            return
        w.ai_category_id, w.ai_category_name = cid, name
    rows = await answers(w.ai_category_id)
    threats = [n for n in w.narratives if n.threat_type != "praise" and n.severity >= 1][:10]
    for n in threats:
        n.ai_exposure = match(w, n, rows)
        try:
            if n.ai_exposure["hosts"]:
                n.ai_exposure["citation_counts"] = await citation_counts(w.ai_category_id, n.ai_exposure["hosts"])
        except Exception as exc:  # noqa: BLE001
            n.ai_exposure["citation_counts_error"] = str(exc)[:120]
    w.ai_status = f"ok: {len(rows)} AI answers checked in {w.ai_category_name}"
