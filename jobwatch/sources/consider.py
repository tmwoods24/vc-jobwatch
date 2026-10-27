"""Consider boards (USV, Promus, and a16z's data).

How it works (verified against jobs.usv.com, Oct 2026):
  * GET https://{domain}/jobs  -> page HTML contains "csrfToken":"..." and sets a session cookie.
  * POST https://{domain}/api-boards/search-jobs  with header  x-csrf-token: <token>
      body: {"meta": {"size": 100, "sequence": <cursor or omitted>},
             "board": {"id": "<board-id>", "isParent": true},
             "query": {...filters...}, "grouped": false}
    Without the token (or with a stale one) the API answers 412 INVALID_CSRF.
  * Response: {"jobs": [...flat list...], "meta": {"sequence": "<next cursor>"}, "total": N}
  * `grouped: false` returns every job; the website groups by company and shows 3 per company.
  * query {"locations": ["New York, New York"]} and {"remoteOnly": true} filter server-side.

a16z's own site (jobs.a16z.com) is a custom front-end, but its data lives on Consider under
board id "andreessen-horowitz", so we read it through another Consider front-end (`api_domain`).
"""
from __future__ import annotations

import logging
import re

from ..models import Job, format_salary

log = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 100
DEFAULT_QUERIES = [{"locations": ["New York, New York"]}, {"remoteOnly": True}]


def _csrf(session, domain: str) -> str:
    html = session.get(f"https://{domain}/jobs", timeout=30).text
    m = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError(f"No csrfToken found on https://{domain}/jobs")
    return m.group(1)


def fetch_consider(src: dict, session, throttle) -> list[Job]:
    api_domain = src.get("api_domain") or src["domain"]
    board_id = src["board_id"]
    token = _csrf(session, api_domain)
    url = f"https://{api_domain}/api-boards/search-jobs"
    headers = {"Accept": "application/json", "Content-Type": "application/json", "x-csrf-token": token,
               "Origin": f"https://{api_domain}", "Referer": f"https://{api_domain}/jobs"}

    by_id: dict[str, Job] = {}
    for query in src.get("server_queries", DEFAULT_QUERIES):
        seq = None
        got = 0
        for _ in range(MAX_PAGES):
            throttle.wait()
            meta = {"size": PAGE_SIZE}
            if seq:
                meta["sequence"] = seq
            body = {"meta": meta, "board": {"id": board_id, "isParent": True}, "query": query, "grouped": False}
            r = session.post(url, json=body, headers=headers, timeout=30)
            if r.status_code == 412:  # token expired mid-run: refresh once
                headers["x-csrf-token"] = _csrf(session, api_domain)
                r = session.post(url, json=body, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            batch = data.get("jobs", [])
            for j in batch:
                job = parse_consider_job(j, src)
                prev = by_id.get(job.external_id)
                if prev:  # same job returned by both the NYC and the remote query
                    prev.remote = prev.remote or job.remote
                else:
                    by_id[job.external_id] = job
            got += len(batch)
            seq = (data.get("meta") or {}).get("sequence")
            total = data.get("total")
            if not batch or not seq or (total is not None and got >= total):
                break
    jobs = list(by_id.values())
    log.info("%s: %d jobs (board %s via %s)", src["name"], len(jobs), board_id, api_domain)
    return jobs


def parse_consider_job(j: dict, src: dict) -> Job:
    company = j.get("companyName") or (j.get("company") or {}).get("name", "")
    locs = list(j.get("locations") or [])
    norm = [n.get("value") for n in j.get("normalizedLocations", []) if isinstance(n, dict) and n.get("value")]
    for n in norm:  # normalized values ("New York, New York") help the location filter
        if n not in locs:
            locs.append(n)
    sal = j.get("salary") or {}
    salary = ""
    if sal.get("minValue") or sal.get("maxValue"):
        salary = format_salary(sal.get("minValue"), sal.get("maxValue"),
                               (sal.get("currency") or {}).get("value", "USD"),
                               (sal.get("period") or {}).get("value", "year"))
    tags = [s.get("label") for s in j.get("jobSeniorities", []) if isinstance(s, dict) and s.get("label")]
    funding = j.get("fundingLV") or {}
    if funding.get("label"):
        tags.append(funding["label"])
    job_id = str(j.get("jobId") or j.get("id") or j.get("url"))
    # `url` is the clean company/ATS posting; `applyUrl` adds a referral tag for the board.
    url = j.get("url") or j.get("applyUrl") or ""
    return Job(
        source=src["name"], group=src.get("group", "vc"), external_id=job_id,
        title=(j.get("title") or "").strip(), company=company.strip(), url=url,
        locations=locs, remote=bool(j.get("remote")) or any("remote" in l.lower() for l in locs),
        posted=(j.get("timeStamp") or "")[:10], salary=salary, funds=[src["name"]], tags=tags,
    )
