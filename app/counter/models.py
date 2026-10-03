"""Counter mode: competitor weakness x our surplus inventory -> ad drafts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..models import Action, Item, LogLine, now_iso, short_id


class CounterInput(BaseModel):
    our_brand: str = Field(min_length=1, max_length=120)
    competitor: str = Field(min_length=1, max_length=120)
    competitor_product: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=120, description="e.g. waterproof running shoes")
    keywords: list[str] = Field(default_factory=list)
    since: str = ""
    until: str = ""
    inventory_source: Literal["shopify", "csv"] = "shopify"
    csv_text: str = Field(default="", max_length=500_000)
    landing_url: str = Field(default="", max_length=500)
    # Business rules (all editable in the UI)
    dir_threshold_days: float = Field(default=60, ge=1, le=3650)
    min_margin_pct: float = Field(default=30, ge=0, le=100)
    cac_share_of_profit: float = Field(default=50, ge=1, le=100, description="Max % of unit gross profit we will pay to acquire a sale")
    campaign_days: int = Field(default=30, ge=1, le=365)


class Sku(BaseModel):
    sku: str
    name: str
    variant: str = ""
    units_on_hand: int = 0
    units_sold_window: int = 0
    window_days: int = 28
    price: float = 0.0
    unit_cost: float | None = None
    description: str = ""          # brand-published copy; the only allowed source of feature claims
    url: str = ""
    # computed
    weekly_velocity: float = 0.0
    days_of_inventory: float | None = None   # None = no sales in window
    margin_pct: float | None = None
    max_cac: float | None = None
    surplus_units: int = 0
    flagged: bool = False
    flag_reason: str = ""


class Cluster(BaseModel):
    id: str = Field(default_factory=short_id)
    friction: str                   # durability, price/value, fit, leaks/waterproofing, shipping, support...
    summary: str = ""
    item_ids: list[str] = Field(default_factory=list)
    mentions: int = 0
    first_hand: int = 0
    platforms: list[str] = Field(default_factory=list)
    engagement: int = 0
    recent_7d: int = 0
    prior_7d: int = 0
    quotes: list[dict] = Field(default_factory=list)    # [{text, url, platform}]
    reality: Literal["verified", "unverified", "disputed", "pending"] = "pending"
    reality_reason: str = ""


class Match(BaseModel):
    id: str = Field(default_factory=short_id)
    cluster_id: str
    sku: str
    fit: float = 0.0
    evidence: str = ""             # exact phrase from the SKU description
    angle: str = ""


class CounterCase(BaseModel):
    mode: Literal["counter"] = "counter"
    id: str = Field(default_factory=short_id)
    created_at: str = Field(default_factory=now_iso)
    input: CounterInput
    status: Literal["queued", "running", "done", "failed"] = "queued"
    stage: str = "queued"
    skus: list[Sku] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    matches: list[Match] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    log: list[LogLine] = Field(default_factory=list)
    sources_status: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    elapsed_seconds: float = 0.0
    replay: bool = False
