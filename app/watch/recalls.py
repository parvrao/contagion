"""FDA recall lookup for CPG safety and ingredient claims.

Uses the public openFDA food enforcement API (no key needed at low volume).
A match is evidence a recall exists; NO match is not proof there is none
(USDA covers meat/poultry, and new recalls can take days to post), so the
UI and the plan treat "no record" as "not confirmed", never as "false".
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from .. import http

API = "https://api.fda.gov/food/enforcement.json"
SEARCH_PAGE = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"
CHECK_TYPES = {"safety", "ingredient"}


def applies(industry: str, threat_type: str, claim: str) -> bool:
    return (industry or "").lower() == "cpg" and (threat_type in CHECK_TYPES or "recall" in (claim or "").lower())


def _term(s: str) -> str:
    return '"' + s.replace('"', "").strip() + '"'


async def check(brand: str, product: str = "", limit: int = 5) -> dict:
    name = (brand or "").strip()
    if not name:
        return {}
    fields = [f"recalling_firm:{_term(name)}", f"product_description:{_term(name)}"]
    if product:
        fields.append(f"product_description:{_term(product)}")
    search = "+".join(fields)   # openFDA: '+' between clauses means OR
    url = f"{API}?search={quote(search, safe=':+')}&sort=report_date:desc&limit={limit}"
    out = {"source": "openFDA food enforcement", "query": search, "url": url, "browse": SEARCH_PAGE,
           "checked": True, "total": 0, "items": [], "error": ""}
    try:
        data = await asyncio.wait_for(http.get_json(url, attempts=2), timeout=15)
    except http.UpstreamError as exc:
        if exc.status == 404:   # openFDA answers 404 NOT_FOUND when nothing matches
            out["note"] = "No FDA food recall on record for this name. Not proof there is none (USDA covers meat; new recalls post with a delay)."
            return out
        out["error"] = str(exc)[:200]
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"FDA lookup failed: {str(exc)[:160]}"
        return out
    out["total"] = int(((data.get("meta") or {}).get("results") or {}).get("total") or 0)
    for r in (data.get("results") or [])[:limit]:
        d = str(r.get("report_date") or r.get("recall_initiation_date") or "")
        out["items"].append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d,
            "firm": r.get("recalling_firm", ""),
            "product": str(r.get("product_description", ""))[:200],
            "reason": str(r.get("reason_for_recall", ""))[:240],
            "classification": r.get("classification", ""),
            "status": r.get("status", ""),
            "number": r.get("recall_number", ""),
        })
    out["note"] = (f"{out['total']} FDA food recall record(s) match this name; check the dates and products against the claim."
                   if out["total"] else "No FDA food recall on record for this name.")
    return out


def prompt_line(rc: dict) -> str:
    if not rc or not rc.get("checked"):
        return "not checked"
    if rc.get("error"):
        return f"lookup failed ({rc['error'][:80]}); treat recall status as unconfirmed"
    if not rc.get("items"):
        return "no FDA food recall on record for this name (not proof there is none; do not call the claim false on this alone)"
    rows = "; ".join(f"{i['date']} {i['classification']} {i['status']}: {i['product'][:80]} ({i['reason'][:80]})" for i in rc["items"][:3])
    return f"{rc['total']} record(s). Most recent: {rows}. Compare dates and products with the claim before using."
