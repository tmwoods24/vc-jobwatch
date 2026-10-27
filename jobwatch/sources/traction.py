"""tractionjobs.com: ~50k roles at VC-backed startups, refreshed daily, tagged with investors.

The site loads static JSON per role family:  https://tractionjobs.com/data/{category}.json
categories: ops (operations, finance & marketing), leadership, sales, tech, autre (other)
Each row uses short keys (verified Oct 2026):
  t title | s startup | f funds ("A ; B") | l city | p country | g region | d posted date
  n first-seen date | u apply URL | $ salary text | r remote flag | k sector
"""
from __future__ import annotations

import hashlib
import logging

from ..models import Job

log = logging.getLogger(__name__)


def fetch_traction(src: dict, session, throttle):
    domain = src.get("domain", "tractionjobs.com")
    jobs = []
    for cat in src.get("categories", ["ops", "leadership"]):
        throttle.wait()
        r = session.get(f"https://{domain}/data/{cat}.json", timeout=60, headers={"Accept": "application/json"})
        r.raise_for_status()
        rows = r.json()
        for row in rows:
            jobs.append(parse_traction_row(row, src))
        log.info("%s/%s: %d rows", src["name"], cat, len(rows))
    return jobs


def parse_traction_row(row: dict, src: dict) -> Job:
    url = row.get("u", "")
    funds = [f.strip() for f in (row.get("f") or "").split(";") if f.strip()]
    city, country = (row.get("l") or "").strip(), (row.get("p") or "").strip()
    loc = city if (not country or country.lower() in city.lower()) else (f"{city}, {country}" if city else country)
    return Job(
        source=src["name"], group=src.get("group", "aggregator"),
        external_id=hashlib.sha1(url.encode()).hexdigest()[:16],
        title=(row.get("t") or "").strip(), company=(row.get("s") or "").strip(), url=url,
        locations=[loc] if loc else [], remote=bool(row.get("r")),
        posted=row.get("d") or row.get("n") or "", salary=row.get("$") or "",
        funds=funds, tags=[row["k"]] if row.get("k") else [],
    )
