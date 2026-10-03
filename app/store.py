"""Case storage (JSON files) + a small pub/sub for live progress.

JSON on disk keeps the app dependency-free and makes every run a file you can
replay on stage if the venue Wi-Fi dies. Swap for Postgres later if needed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .config import settings
from .models import Case


def _load(text: str):
    d = json.loads(text)
    if d.get("mode") == "counter":
        from .counter.models import CounterCase
        return CounterCase.model_validate(d)
    return Case.model_validate(d)

CASES_DIR = settings.data_dir / "cases"
REPLAYS_DIR = settings.data_dir / "replays"
CASES_DIR.mkdir(parents=True, exist_ok=True)
REPLAYS_DIR.mkdir(parents=True, exist_ok=True)

_cases: dict[str, Case] = {}
_subscribers: dict[str, set[asyncio.Queue]] = {}


def _path(case_id: str) -> Path:
    return CASES_DIR / f"{case_id}.json"


def save(case) -> None:
    _cases[case.id] = case
    tmp = _path(case.id).with_suffix(".tmp")
    tmp.write_text(case.model_dump_json(indent=1))
    tmp.replace(_path(case.id))


def get(case_id: str):
    if case_id in _cases:
        return _cases[case_id]
    p = _path(case_id)
    if p.exists():
        case = _load(p.read_text())
        _cases[case.id] = case
        return case
    return None


def list_cases() -> list[dict]:
    rows = []
    for p in sorted(CASES_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        ci = d["input"]
        counter = d.get("mode") == "counter"
        rows.append({
            "id": d["id"], "mode": d.get("mode", "defend"),
            "brand": ci["our_brand"] if counter else ci["brand"],
            "claim": f"vs. {ci['competitor']} {ci.get('competitor_product', '')}".strip() if counter else ci["claim"],
            "summary": d.get("summary", {}),
            "status": d["status"], "created_at": d["created_at"], "grade": d.get("grade", {}),
            "replay": d.get("replay", False),
        })
    return rows


def list_replays() -> list[dict]:
    out = []
    for p in sorted(REPLAYS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text())
            ci = d["input"]
            counter = d.get("mode") == "counter"
            out.append({"name": p.stem, "mode": d.get("mode", "defend"),
                        "brand": ci["our_brand"] if counter else ci["brand"],
                        "claim": f"vs. {ci['competitor']} {ci.get('competitor_product', '')}".strip() if counter else ci["claim"],
                        "recorded_at": d["created_at"]})
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return out


def subscribe(case_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.setdefault(case_id, set()).add(q)
    return q


def unsubscribe(case_id: str, q: asyncio.Queue) -> None:
    _subscribers.get(case_id, set()).discard(q)


def publish(case_id: str, event: dict) -> None:
    for q in list(_subscribers.get(case_id, set())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
