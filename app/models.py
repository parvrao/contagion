"""Data shapes shared by the pipeline, the API and the store."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short_id() -> str:
    return uuid.uuid4().hex[:10]


Stance = Literal["amplifies", "debunks", "reports", "unrelated", "unknown"]
Verdict = Literal["repeats", "corrects", "unaware", "error"]
ActionKind = Literal["fact_sheet", "site_fix", "correction_request", "platform_flag", "ad_package"]
ActionStatus = Literal["pending", "approved", "rejected"]


class CaseInput(BaseModel):
    brand: str = Field(min_length=1, max_length=120)
    claim: str = Field(min_length=5, max_length=600, description="The rumor, stated the way people repeat it")
    truth: str = Field(min_length=5, max_length=2000, description="What is actually true, from the brand")
    truth_url: str = Field(default="", max_length=500)
    keywords: list[str] = Field(default_factory=list, description="Extra search phrases")
    since: str = Field(default="", description="YYYY-MM-DD, optional")
    until: str = Field(default="", description="YYYY-MM-DD, optional")


class Item(BaseModel):
    """One public instance of the rumor (a post, article, video, thread)."""
    id: str = Field(default_factory=short_id)
    platform: str
    url: str
    domain: str = ""            # publisher domain (Google News links are redirects, so we keep this separately)
    title: str = ""
    text: str = ""
    author: str = ""            # public handle or outlet name only
    published_at: str = ""      # ISO 8601, may be empty when the source gives no date
    date_source: str = "platform"  # platform | search index | model-reported
    engagement: int = 0         # upvotes / points / comments, whatever the source exposes
    views: int = 0              # when the platform exposes it (YouTube)
    comments: int = 0
    outbound_links: list[str] = Field(default_factory=list)
    query: str = ""
    stance: Stance = "unknown"
    stance_reason: str = ""
    quote: str = ""             # verbatim telling sentence (counter mode)
    confirmed: bool = False     # survived the skeptic pass


class Probe(BaseModel):
    """One question asked to one AI engine."""
    id: str = Field(default_factory=short_id)
    engine: str
    mode: str = ""              # "live web" or "model memory"
    question: str
    answer: str = ""
    citations: list[dict] = Field(default_factory=list)   # [{url, title}]
    verdict: Verdict = "unaware"
    verdict_reason: str = ""
    confirmed: bool = False     # 'repeats' verdict survived the skeptic pass
    cited_item_ids: list[str] = Field(default_factory=list)
    error: str = ""


class Action(BaseModel):
    id: str = Field(default_factory=short_id)
    kind: ActionKind
    title: str
    target_url: str = ""
    platform: str = ""
    draft: str
    guardrail_flags: list[str] = Field(default_factory=list)
    status: ActionStatus = "pending"
    rationale: str = ""         # why the system proposed this action
    meta: dict = Field(default_factory=dict)  # structured fields (ad packages: hooks, budget, sku...)
    final_text: str = ""
    decided_at: str = ""
    decided_note: str = ""      # reviewer's note


class LogLine(BaseModel):
    at: str = Field(default_factory=now_iso)
    stage: str
    message: str
    level: Literal["info", "warn", "error"] = "info"


class Case(BaseModel):
    mode: Literal["defend"] = "defend"
    id: str = Field(default_factory=short_id)
    created_at: str = Field(default_factory=now_iso)
    input: CaseInput
    status: Literal["queued", "running", "done", "failed"] = "queued"
    stage: str = "queued"
    items: list[Item] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    spread: dict = Field(default_factory=dict)
    grade: dict = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list)
    log: list[LogLine] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)   # grade snapshots, one per run / re-check
    sources_status: dict = Field(default_factory=dict)  # source name -> "ok (n)" / "skipped: reason" / "error: ..."
    elapsed_seconds: float = 0.0
    replay: bool = False
