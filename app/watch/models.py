"""Watch mode: a live, always-on monitor for one brand.

A Watch keeps polling public sources, triages every new mention, groups them
into narratives, scores each narrative, raises alerts, and drafts a mitigation
playbook a person approves. Nothing is posted or sent by the system.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..models import LogLine, now_iso, short_id

Sentiment = Literal["negative", "neutral", "positive", "mixed"]
ThreatType = Literal[
    "rumor", "safety", "product_issue", "pricing", "boycott", "legal",
    "pr_crisis", "service_outage", "competitor", "praise", "general",
]
Stance = Literal["spreading", "correcting", "neutral"]
NarrativeStatus = Literal["emerging", "escalating", "active", "fading"]
ResponseLevel = Literal["monitor", "prepare", "respond", "escalate"]
AlertLevel = Literal["info", "warn", "critical"]
ActionStatus = Literal["pending", "approved", "rejected"]


class WatchInput(BaseModel):
    brand: str = Field(min_length=1, max_length=120)
    product: str = Field(default="", max_length=120)
    domain: str = Field(default="", max_length=200)
    keywords: list[str] = Field(default_factory=list)
    position: str = Field(default="", max_length=2000, description="Optional brand facts drafts may use")
    brand_audience: int = Field(default=0, ge=0, description="Followers/reach of the brand's main channel; 0 = unknown")


class Mention(BaseModel):
    id: str = Field(default_factory=short_id)
    platform: str
    url: str
    domain: str = ""
    title: str = ""
    text: str = ""
    author: str = ""
    published_at: str = ""
    found_at: str = Field(default_factory=now_iso)
    engagement: int = 0
    source: str = ""
    # triage
    triaged: bool = False
    relevant: bool = True
    sentiment: Sentiment = "neutral"
    threat_type: ThreatType = "general"
    severity: int = 0            # 0 none, 1 low, 2 elevated, 3 high
    is_claim: bool = False
    stance: Stance = "neutral"
    summary: str = ""
    narrative_id: str = ""
    labeled_by: str = ""         # Claude | Gemini | keywords


class WatchAction(BaseModel):
    id: str = Field(default_factory=short_id)
    narrative_id: str
    kind: str                    # holding_statement | social_reply | support_macro | faq_update | correction_request | internal_brief | task
    title: str
    owner: str = ""
    timing: str = ""
    channel: str = ""
    target_url: str = ""
    why: str = ""
    draft: str = ""
    status: ActionStatus = "pending"
    final_text: str = ""
    decided_at: str = ""
    decided_note: str = ""
    flags: list[str] = Field(default_factory=list)
    raci: dict = Field(default_factory=dict)


class Playbook(BaseModel):
    generated_at: str = Field(default_factory=now_iso)
    by: str = "Claude"           # Claude | Gemini | template
    what: str = ""
    who: str = ""
    how_fast: str = ""
    why_it_matters: str = ""
    response_level: ResponseLevel = "monitor"
    level_reason: str = ""
    do_not: list[str] = Field(default_factory=list)
    watch_for: list[str] = Field(default_factory=list)
    # How true is the claim (decided before any plan)
    veracity: str = "unclear"    # false | misframed | true_unflattering | opinion | unclear
    true_part: str = ""
    missing_context: str = ""
    veracity_basis: str = ""
    # Would replying amplify it? (computed, not model-written)
    amplification: dict = Field(default_factory=dict)
    original_source: dict = Field(default_factory=dict)   # {url, author, platform, why}
    scct: dict = Field(default_factory=dict)              # crisis cluster + response strategy (Coombs)
    stakeholders: list[dict] = Field(default_factory=list)  # Mendelow power/interest grid


class Narrative(BaseModel):
    id: str = Field(default_factory=short_id)
    title: str
    summary: str = ""
    claim: str = ""              # the claim as people state it (for deep trace)
    threat_type: ThreatType = "general"
    severity: int = 0
    mention_ids: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    news_outlets: list[str] = Field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    count: int = 0
    count_24h: int = 0
    velocity: float = 0.0        # last-6h count divided by the average 6h count before
    negative_share: float = 0.0
    spreading: int = 0
    correcting: int = 0
    engagement: int = 0
    spark: list[int] = Field(default_factory=list)   # 24 hourly buckets, oldest first
    spark_days: list[int] = Field(default_factory=list)  # 7 daily buckets, oldest first
    score: int = 0
    score_parts: dict = Field(default_factory=dict)
    status: NarrativeStatus = "emerging"
    playbook: Optional[Playbook] = None
    trace_case_id: str = ""
    ai_exposure: dict = Field(default_factory=dict)   # Profound: AI answers repeating or citing this narrative


class Alert(BaseModel):
    id: str = Field(default_factory=short_id)
    at: str = Field(default_factory=now_iso)
    narrative_id: str = ""
    level: AlertLevel = "info"
    title: str
    reason: str
    read: bool = False
    delivered: list[str] = Field(default_factory=list)


class Watch(BaseModel):
    mode: Literal["watch"] = "watch"
    id: str = Field(default_factory=short_id)
    created_at: str = Field(default_factory=now_iso)
    input: WatchInput
    status: Literal["starting", "running", "paused", "stopped", "failed"] = "starting"
    stage: str = "starting"
    cycles: int = 0
    poll_seconds: int = 90
    last_poll_at: str = ""
    next_poll_at: str = ""
    mentions: list[Mention] = Field(default_factory=list)
    narratives: list[Narrative] = Field(default_factory=list)
    actions: list[WatchAction] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    log: list[LogLine] = Field(default_factory=list)
    sources_status: dict = Field(default_factory=dict)
    source_last_run: dict = Field(default_factory=dict)
    triage_mode: str = ""        # "Claude", "Gemini" or "keyword fallback"
    triage_errors: list[str] = Field(default_factory=list)
    ai_status: str = ""          # Profound status line
    ai_category_id: str = ""
    ai_category_name: str = ""
    ai_checked_at: str = ""
    replay: bool = False
