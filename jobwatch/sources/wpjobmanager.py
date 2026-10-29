"""WordPress "WP Job Manager" boards, e.g. Venture5 (venture5.com/jobs), a board of roles at VC firms.

How it works (verified against venture5.com, Oct 2026):
  POST https://{domain}/jm-ajax/get_listings/   (form-encoded)
    page=N, per_page=100, search_location=<text>, search_keywords=, orderby=date, order=DESC
  -> {"found_jobs": true, "max_num_pages": N, "html": "<li class='post-65528 job_listing ...'>...</li>..."}
  Each <li> holds: <a href=job page>, <h3>title</h3>, <div class=company><strong>name</strong>,
  <div class=location>, <time datetime="YYYY-MM-DD">.
  search_location filters server-side. Listings stay up for months, so `max_age_days` drops old ones.
"""
from __future__ import annotations

import datetime as dt
import html as htmllib
import logging
import re

from ..models import Job

log = logging.getLogger(__name__)

MAX_PAGES = 30
LISTING_START = re.compile(r'(?=<li\b[^>]*class="[^"]*\bjob_listing\b)')


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_listings_html(fragment: str, src: dict) -> list[Job]:
    jobs = []
    # Each listing is an <li class="post-123 job_listing ..."> that itself contains nested <li>s
    # (job types, date), so split on listing starts rather than matching </li>.
    for chunk in LISTING_START.split(fragment):
        head = re.match(r"<li\b([^>]*)>", chunk)
        if not head or "job_listing" not in head.group(1):
            continue
        attrs, body = head.group(1), chunk[head.end():]
        pid = re.search(r"\bpost-(\d+)\b", attrs)
        href = re.search(r'<a[^>]+href="([^"]+)"', body)
        title = re.search(r"<h3[^>]*>(.*?)</h3>", body, re.S)
        company = re.search(r'class="company"[^>]*>.*?<strong>(.*?)</strong>', body, re.S)
        loc = re.search(r'<div class="location"[^>]*>(.*?)</div>', body, re.S)
        date = re.search(r'<time[^>]+datetime="([0-9-]{10})', body)
        types = re.findall(r'<li class="job-type[^"]*"[^>]*>(.*?)</li>', body, re.S)
        location = _clean(loc.group(1)) if loc else ""
        url = htmllib.unescape(href.group(1)) if href else ""
        jobs.append(Job(
            source=src["name"], group=src.get("group", "vc"),
            external_id=pid.group(1) if pid else url,
            title=_clean(title.group(1)) if title else "", company=_clean(company.group(1)) if company else "",
            url=url, locations=[location] if location else [],
            remote="remote" in location.lower() or "remote" in attrs.lower(),
            posted=date.group(1) if date else "", funds=[src["name"]],
            tags=[_clean(t) for t in types if _clean(t)],
        ))
    return jobs


def fetch_wpjobmanager(src: dict, session, throttle) -> list[Job]:
    domain = src["domain"]
    url = f"https://{domain}/jm-ajax/get_listings/"
    max_age = src.get("max_age_days")
    cutoff = (dt.date.today() - dt.timedelta(days=max_age)).isoformat() if max_age else ""
    by_id: dict[str, Job] = {}
    for loc in src.get("server_locations", ["New York", "Remote"]):
        for page in range(1, MAX_PAGES + 1):
            throttle.wait()
            r = session.post(url, timeout=30, data={
                "page": page, "per_page": 100, "search_keywords": "", "search_location": loc,
                "orderby": "date", "order": "DESC"},
                headers={"Accept": "application/json", "Referer": f"https://{domain}/jobs/",
                         "X-Requested-With": "XMLHttpRequest"})
            r.raise_for_status()
            data = r.json()
            batch = parse_listings_html(data.get("html") or "", src)
            for j in batch:
                by_id.setdefault(j.external_id, j)
            # results are newest-first, so stop once a whole page is older than the cutoff
            if not batch or page >= int(data.get("max_num_pages") or 0):
                break
            if cutoff and all(j.posted and j.posted < cutoff for j in batch):
                break
    jobs = [j for j in by_id.values() if not cutoff or not j.posted or j.posted >= cutoff]
    log.info("%s: %d jobs (%s)", src["name"], len(jobs), f"posted since {cutoff}" if cutoff else "all")
    return jobs
