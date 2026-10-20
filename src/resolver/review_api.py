"""Review API (Milestone 7): serves the review queue and records decisions.

    uv run uvicorn resolver.review_api:app --port 8000

Paths (environment variables, relative to the working directory):
  RESOLVER_OUT        `resolver run` output: review.jsonl, splits.jsonl  [out]
  RESOLVER_SOURCES    source CSVs, for the side-by-side records         [data/sources]
  RESOLVER_DECISIONS  decisions CSV, appended to                         [data/review_decisions.csv]
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from resolver.blocking import load_sources
from resolver.review import (
    DecisionValue,
    ReviewQueue,
    build_queue,
    read_decisions,
    read_jsonl,
    record_decision,
)

app = FastAPI(title="Entity resolution review")


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env) or default)


def decisions_path() -> Path:
    return _path("RESOLVER_DECISIONS", "data/review_decisions.csv")


@functools.cache
def get_queue() -> ReviewQueue:
    out = _path("RESOLVER_OUT", "out")
    review, splits = out / "review.jsonl", out / "splits.jsonl"
    for p in (review, splits):
        if not p.exists():
            raise HTTPException(503, f"{p} not found: run `make data resolve` first")
    df = load_sources(_path("RESOLVER_SOURCES", "data/sources"))
    records = {str(r["record_id"]): r for r in df.to_dicts()}
    queue = build_queue(read_jsonl(review), read_jsonl(splits), records)
    queue.decisions = read_decisions(decisions_path())
    return queue


class DecisionIn(BaseModel):
    item_id: str
    decision: DecisionValue


@app.get("/api/queue")
def queue() -> dict[str, Any]:
    """Groups with counts, and every item's summary in queue order."""
    return get_queue().summary()


@app.get("/api/items/{item_id}")
def item(item_id: str) -> dict[str, Any]:
    """One item: records side by side, field agreement, scores and evidence."""
    try:
        return get_queue().detail(item_id)
    except KeyError:
        raise HTTPException(404, f"unknown item {item_id}") from None


@app.post("/api/decisions")
def decide(body: DecisionIn) -> dict[str, str]:
    """Append an Approve / Reject to the decisions CSV; the latest one wins."""
    try:
        return record_decision(
            decisions_path(), get_queue(), body.item_id, body.decision
        )
    except KeyError:
        raise HTTPException(404, f"unknown item {body.item_id}") from None
