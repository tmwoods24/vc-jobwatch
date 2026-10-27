"""Getro boards (FirstMark, Lerer Hippeau, Greycroft, Thrive, GC, Khosla, EIP, Lux, BEV, Prelude, ...).

How it works (verified against jobs.firstmark.com, Oct 2026):
  * The board page embeds  window.__GETRO_BOARD_POLICY={"network_id":"45303",...}
  * Jobs come from  POST https://api.getro.com/api/v2/collections/{network_id}/search/jobs
      body: {"hitsPerPage": 20, "page": N, "filters": {"page": N, "searchable_locations": [...]}, "query": ""}
      headers: Accept: application/json  (without it the API returns an empty body)
  * Response: {"results": {"jobs": [...], "count": <total>}}. hitsPerPage is capped at 20.
  * `searchable_locations` filters server-side; values in a list are OR'd.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from ..models import Job, format_salary

log = logging.getLogger(__name__)

API = "https://api.getro.com/api/v2/collections/{nid}/search/jobs"
PAGE_SIZE = 20
MAX_PAGES = 200

# Server-side location prefilter. Client-side filters run afterwards anyway.
DEFAULT_LOCATIONS = ["New York, NY, USA", "Remote"]


def discover_network_id(session, domain: str) -> str:
    html = session.get(f"https://{domain}/jobs", timeout=30).text
    for pat in (r'"network_id"\s*:\s*"?(\d+)', r"collections/(\d+)/", r'"collectionId"\s*:\s*"?(\d+)'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise RuntimeError(f"Could not find Getro network_id on https://{domain}/jobs")


def fetch_getro(src: dict, session, throttle) -> list[Job]:
    domain = src["domain"]
    nid = str(src.get("network_id") or discover_network_id(session, domain))
    locations = src.get("server_locations", DEFAULT_LOCATIONS)
    url = API.format(nid=nid)
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "Origin": f"https://{domain}", "Referer": f"https://{domain}/jobs"}

    jobs: list[Job] = []
    total = None
    for page in range(MAX_PAGES):
        throttle.wait()
        body = {"hitsPerPage": PAGE_SIZE, "page": page,
                "filters": {"page": page, "searchable_locations": locations}, "query": ""}
        r = session.post(url, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        res = r.json().get("results", {})
        batch = res.get("jobs", [])
        total = res.get("count", total)
        jobs.extend(parse_getro_job(j, src) for j in batch)
        if not batch or (total is not None and len(jobs) >= total):
            break
    log.info("%s: %d jobs (network %s, server total %s)", src["name"], len(jobs), nid, total)
    return jobs


def parse_getro_job(j: dict, src: dict) -> Job:
    org = j.get("organization") or j.get("company") or {}
    domain = src["domain"]
    # Link to the board's own page for the job when we can; it shows the description.
    if org.get("slug") and j.get("slug"):
        url = f"https://{domain}/companies/{org['slug']}/jobs/{j['slug']}"
    else:
        url = j.get("url", "")
    created = j.get("created_at")
    posted = ""
    if isinstance(created, (int, float)):
        posted = dt.datetime.fromtimestamp(created, dt.timezone.utc).date().isoformat()
    elif isinstance(created, str):
        posted = created[:10]
    locs = j.get("locations") or [d.get("name") for d in j.get("location_details", []) if d.get("name")]
    remote = j.get("work_mode") == "remote" or any("remote" in (l or "").lower() for l in locs)
    salary = ""
    if j.get("compensation_public", True):
        if "compensation_amount_min_cents" in j or "compensation_amount_max_cents" in j:
            salary = format_salary(j.get("compensation_amount_min_cents"), j.get("compensation_amount_max_cents"),
                                   j.get("compensation_currency"), j.get("compensation_period"), cents=True)
        else:  # Lowercarbon's feed uses whole-unit min/max
            salary = format_salary(j.get("compensation_min"), j.get("compensation_max"),
                                   j.get("compensation_currency"), j.get("compensation_period"))
    tags = [t for t in [org.get("stage"), j.get("seniority")] if t and t not in ("other", "series_unknown")]
    return Job(
        source=src["name"], group=src.get("group", "vc"), external_id=str(j.get("id")),
        title=(j.get("title") or "").strip(), company=(org.get("name") or "").strip(), url=url,
        locations=[l for l in locs if l], remote=remote, posted=posted, salary=salary,
        funds=[src["name"]], tags=tags,
    )
