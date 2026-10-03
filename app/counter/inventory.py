"""Inventory ingestion + the surplus rule.

Sources:
- Shopify Admin GraphQL. Dev Dashboard apps use the client credentials grant
  (client id + secret -> 24 h token, refreshed automatically). A legacy
  `shpat_` token in SHOPIFY_ACCESS_TOKEN also works.
- CSV with the same fields, as a fallback.

Velocity = units sold in the last N days from orders (read_orders scope).
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .. import http
from .models import CounterInput, Sku

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-07")
WINDOW_DAYS = int(os.environ.get("SHOPIFY_VELOCITY_DAYS", "28"))
_token = {"value": "", "expires": 0.0}

CSV_FIELDS = ["sku", "name", "units_on_hand", "units_sold_28d", "price", "unit_cost", "description", "url"]


class InventoryError(RuntimeError):
    pass


def shopify_configured() -> tuple[bool, str]:
    store = os.environ.get("SHOPIFY_STORE", "")
    if not store:
        return False, "set SHOPIFY_STORE"
    if os.environ.get("SHOPIFY_ACCESS_TOKEN") or (os.environ.get("SHOPIFY_CLIENT_ID") and os.environ.get("SHOPIFY_CLIENT_SECRET")):
        return True, ""
    return False, "set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET (or SHOPIFY_ACCESS_TOKEN)"


def _store() -> str:
    s = os.environ["SHOPIFY_STORE"].strip().removeprefix("https://").rstrip("/")
    return s if s.endswith(".myshopify.com") else f"{s}.myshopify.com"


async def _access_token() -> str:
    legacy = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
    if legacy:
        return legacy
    if _token["value"] and _token["expires"] > time.time() + 300:
        return _token["value"]
    try:
        resp = await http.request(
            "POST",
            f"https://{_store()}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ["SHOPIFY_CLIENT_ID"],
                "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"],
            },
            attempts=2,
        )
    except http.UpstreamError as exc:
        hint = " (app and store must be in the same Shopify organization)" if "not_permitted" in str(exc) else ""
        raise InventoryError(f"Shopify token request failed: {exc}{hint}") from exc
    data = resp.json()
    _token.update(value=data["access_token"], expires=time.time() + float(data.get("expires_in", 86399)))
    return _token["value"]


async def _gql(query: str, variables: dict) -> dict:
    token = await _access_token()
    data = await http.post_json(
        f"https://{_store()}/admin/api/{API_VERSION}/graphql.json",
        {"query": query, "variables": variables},
        headers={"X-Shopify-Access-Token": token},
    )
    if data.get("errors"):
        raise InventoryError(f"Shopify GraphQL error: {str(data['errors'])[:300]}")
    # Shopify throttles by query cost; back off if we're close to empty.
    cost = (data.get("extensions") or {}).get("cost", {}).get("throttleStatus", {})
    if cost and cost.get("currentlyAvailable", 1000) < 100:
        import asyncio
        await asyncio.sleep(2)
    return data["data"]


VARIANTS_Q = """
query Variants($cursor: String) {
  productVariants(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id sku title price inventoryQuantity
      inventoryItem { unitCost { amount } }
      product { title description onlineStoreUrl status }
    }
  }
}"""

ORDERS_Q = """
query Orders($cursor: String, $q: String) {
  orders(first: 100, after: $cursor, query: $q) {
    pageInfo { hasNextPage endCursor }
    nodes { lineItems(first: 100) { nodes { quantity variant { id } } } }
  }
}"""


async def load_shopify() -> list[Sku]:
    variants, cursor = [], None
    for _ in range(50):  # hard page cap
        d = (await _gql(VARIANTS_Q, {"cursor": cursor}))["productVariants"]
        variants += d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            break
        cursor = d["pageInfo"]["endCursor"]

    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    sold: dict[str, int] = {}
    cursor = None
    for _ in range(50):
        d = (await _gql(ORDERS_Q, {"cursor": cursor, "q": f"created_at:>={since}"}))["orders"]
        for o in d["nodes"]:
            for li in o["lineItems"]["nodes"]:
                vid = (li.get("variant") or {}).get("id")
                if vid:
                    sold[vid] = sold.get(vid, 0) + int(li.get("quantity") or 0)
        if not d["pageInfo"]["hasNextPage"]:
            break
        cursor = d["pageInfo"]["endCursor"]

    out = []
    for v in variants:
        p = v.get("product") or {}
        if p.get("status") and p["status"] != "ACTIVE":
            continue
        cost = ((v.get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
        out.append(Sku(
            sku=v.get("sku") or v["id"].rsplit("/", 1)[-1],
            name=p.get("title", ""),
            variant="" if v.get("title") == "Default Title" else v.get("title", ""),
            units_on_hand=int(v.get("inventoryQuantity") or 0),
            units_sold_window=sold.get(v["id"], 0),
            window_days=WINDOW_DAYS,
            price=float(v.get("price") or 0),
            unit_cost=float(cost) if cost not in (None, "") else None,
            description=(p.get("description") or "")[:2000],
            url=p.get("onlineStoreUrl") or "",
        ))
    return out


def load_csv(text: str) -> list[Sku]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    missing = {"sku", "name", "units_on_hand", "price"} - set(reader.fieldnames or [])
    if missing:
        raise InventoryError(f"CSV is missing columns: {', '.join(sorted(missing))}. Expected: {', '.join(CSV_FIELDS)}")
    out = []
    for n, row in enumerate(reader, start=2):
        try:
            out.append(Sku(
                sku=row["sku"].strip(), name=row["name"].strip(),
                units_on_hand=int(float(row["units_on_hand"] or 0)),
                units_sold_window=int(float(row.get("units_sold_28d") or 0)), window_days=28,
                price=_money(row["price"]),
                unit_cost=_money(row["unit_cost"]) if (row.get("unit_cost") or "").strip() else None,
                description=(row.get("description") or "").strip(), url=(row.get("url") or "").strip(),
            ))
        except (ValueError, KeyError) as exc:
            raise InventoryError(f"CSV row {n}: {exc}") from exc
    return out


def _money(s: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", str(s)) or 0)


def apply_rules(skus: list[Sku], ci: CounterInput) -> None:
    """Days of inventory, margin, max CAC, surplus flag. Pure arithmetic, no model."""
    for s in skus:
        s.weekly_velocity = round(s.units_sold_window / s.window_days * 7, 2) if s.window_days else 0.0
        daily = s.units_sold_window / s.window_days if s.window_days else 0
        s.days_of_inventory = round(s.units_on_hand / daily, 1) if daily > 0 else None
        if s.unit_cost is not None and s.price > 0:
            profit = s.price - s.unit_cost
            s.margin_pct = round(profit / s.price * 100, 1)
            s.max_cac = round(max(profit, 0) * ci.cac_share_of_profit / 100, 2)
        target_units = daily * ci.dir_threshold_days
        s.surplus_units = max(0, int(s.units_on_hand - target_units))
        s.stockout_risk = s.days_of_inventory is not None and s.days_of_inventory < ci.stockout_days
        s.revenue_unlocked = round(s.surplus_units * s.price, 2)
        if s.unit_cost is not None:
            s.cash_tied = round(s.surplus_units * s.unit_cost, 2)
            s.holding_cost_month = round(s.cash_tied * ci.holding_cost_pct_year / 100 / 12, 2) if ci.holding_cost_pct_year else None
            s.contribution_after_cac = round(s.surplus_units * (s.price - s.unit_cost - (s.max_cac or 0)), 2)

        reasons, ok = [], True
        if s.units_on_hand <= 0:
            ok, reasons = False, ["out of stock"]
        elif s.stockout_risk:
            ok = False
            reasons.append(f"stockout risk: {s.days_of_inventory:g} days of supply, do not advertise")
        elif s.days_of_inventory is not None and s.days_of_inventory <= ci.dir_threshold_days:
            ok = False
            reasons.append(f"{s.days_of_inventory:g} days of supply (at or under {ci.dir_threshold_days:g})")
        else:
            reasons.append("no sales in window" if s.days_of_inventory is None else f"{s.days_of_inventory:g} days of supply")
        if s.margin_pct is None:
            ok = False
            reasons.append("unit cost missing, margin unknown")
        elif s.margin_pct < ci.min_margin_pct:
            ok = False
            reasons.append(f"margin {s.margin_pct:g}% under {ci.min_margin_pct:g}%")
        else:
            reasons.append(f"margin {s.margin_pct:g}%")
        s.flagged, s.flag_reason = ok, "; ".join(reasons)


def impact(skus: list[Sku], ci: CounterInput) -> dict:
    """Dollar view of the surplus. Arithmetic on inventory data plus one stated assumption (holding %)."""
    flagged = [s for s in skus if s.flagged]
    tot = lambda attr: round(sum((getattr(s, attr) or 0) for s in flagged), 2)
    return {
        "cash_tied": tot("cash_tied"),
        "holding_cost_month": tot("holding_cost_month") if ci.holding_cost_pct_year else None,
        "revenue_unlocked": tot("revenue_unlocked"),
        "contribution_after_cac": tot("contribution_after_cac"),
        "holding_pct_assumption": ci.holding_cost_pct_year or None,
        "stockout_guard": [{"sku": s.sku, "name": s.name, "days_of_inventory": s.days_of_inventory} for s in skus if s.stockout_risk],
    }
