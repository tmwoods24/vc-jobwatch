"""Scores each new job 1-10 for fit against profile.md using the Claude API."""
from __future__ import annotations

import json
import logging
import os
import re

import requests

from .models import Job

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """You screen job postings for one specific candidate. You will get the candidate's profile and a
numbered list of postings (title, company, location, investors, tags). For each posting, return a fit
score from 1 to 10 and a one-sentence reason (max 25 words) a busy reader can act on.

Scoring guide:
 9-10  squarely one of the candidate's top targets, right seniority, NYC-compatible
 7-8   strong fit with one caveat (seniority, sector, scope unclear)
 5-6   plausible; worth a glance
 3-4   weak fit (wrong level, mostly sales/admin, unclear NYC)
 1-2   not a fit
Judge from the title, company and investors; you do not have the full description, so say so when the
title is ambiguous (e.g. "Chief of Staff" could be EA-like). Penalize non-NYC postings that are not remote.

Reply with ONLY a JSON array, one object per posting, in input order:
[{"i": 1, "score": 7, "reason": "..."}]"""


def _describe(i: int, j: Job) -> str:
    parts = [f"{i}. {j.title} | {j.company} | {j.location_text()}"]
    if j.funds:
        parts.append("investors: " + ", ".join(j.funds[:6]))
    if j.tags:
        parts.append("tags: " + ", ".join(j.tags[:4]))
    if j.salary:
        parts.append("pay: " + j.salary)
    return " | ".join(parts)


def score_jobs(jobs: list[Job], cfg: dict, profile: str) -> None:
    sc = cfg.get("scoring", {})
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.warning("ANTHROPIC_API_KEY not set; skipping fit scoring")
        return
    todo = jobs[: sc.get("max_jobs", 200)]
    size = sc.get("batch_size", 20)
    for start in range(0, len(todo), size):
        batch = todo[start:start + size]
        listing = "\n".join(_describe(i + 1, j) for i, j in enumerate(batch))
        body = {
            "model": sc.get("model", "claude-sonnet-5-5"),
            "max_tokens": 4000,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": f"<profile>\n{profile}\n</profile>\n\n<postings>\n{listing}\n</postings>"}],
        }
        try:
            r = requests.post(API_URL, json=body, timeout=120, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            apply_scores(batch, text)
        except Exception as e:  # keep the report going if scoring hiccups
            log.error("Scoring batch %d failed: %s", start // size + 1, e)


def apply_scores(batch: list[Job], text: str) -> None:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("no JSON array in model reply")
    for item in json.loads(m.group(0)):
        idx = int(item.get("i", 0)) - 1
        if 0 <= idx < len(batch):
            batch[idx].score = max(1, min(10, int(item.get("score", 0) or 0)))
            batch[idx].reason = str(item.get("reason", "")).strip()
