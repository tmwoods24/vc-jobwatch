"""Lowercarbon Capital's portfolio jobs page (WordPress, data mirrored from Getro).

GET https://lowercarbon.com/wp-json/lowercarboncapital/v1/jobs?per_page=100&page=N&title=&companies=&job_functions=
  -> {"items": [...Getro-shaped jobs, company under "company"...], "meta": {"total": N, "total_companies": M}}
Verified Oct 2026. No server-side location filter, so we page through everything (~18 requests).
"""
from __future__ import annotations

import logging

from .getro import parse_getro_job

log = logging.getLogger(__name__)

URL = "https://{domain}/wp-json/lowercarboncapital/v1/jobs"
PAGE_SIZE = 100
MAX_PAGES = 60


def fetch_lowercarbon(src: dict, session, throttle):
    domain = src.get("domain", "lowercarbon.com")
    jobs = []
    total = None
    for page in range(1, MAX_PAGES + 1):
        throttle.wait()
        params = {"per_page": PAGE_SIZE, "page": page, "title": "", "companies": "", "job_functions": ""}
        r = session.get(URL.format(domain=domain), params=params, timeout=30,
                        headers={"Accept": "application/json", "Referer": f"https://{domain}/get-off-the-couch/"})
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        total = (data.get("meta") or {}).get("total", total)
        for j in items:
            job = parse_getro_job(j, src)
            job.url = j.get("url") or job.url  # the WP site has no per-job pages
            jobs.append(job)
        if not items or (total is not None and len(jobs) >= total):
            break
    log.info("%s: %d jobs (server total %s)", src["name"], len(jobs), total)
    return jobs
