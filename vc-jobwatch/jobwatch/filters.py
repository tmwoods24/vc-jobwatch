from __future__ import annotations

import re

from .models import Job

US_HINTS = re.compile(r"united states|\busa?\b|\bus\b|north america|anywhere|global|worldwide", re.I)


class JobFilter:
    def __init__(self, cfg: dict):
        loc = cfg.get("locations", {})
        self.loc_inc = [re.compile(p, re.I) for p in loc.get("include", [])]
        self.allow_remote = loc.get("allow_remote", True)
        self.remote_exc = [re.compile(p, re.I) for p in loc.get("remote_exclude", [])]
        t = cfg.get("titles", {})
        self.title_inc = [re.compile(p, re.I) for p in t.get("include", [])]
        self.title_exc = [re.compile(p, re.I) for p in t.get("exclude", [])]
        self.title_always = [re.compile(p, re.I) for p in t.get("always_include", [])]
        self.title_hard_exc = [re.compile(p, re.I) for p in t.get("hard_exclude", [])]

    def location_ok(self, job: Job) -> bool:
        text = " | ".join(job.locations)
        if any(p.search(text) for p in self.loc_inc):
            return True
        if self.allow_remote and job.remote:
            if any(p.search(text) for p in self.remote_exc) and not US_HINTS.search(text):
                return False  # remote, but restricted to another country/region
            return True
        return False

    def title_ok(self, job: Job) -> bool:
        t = job.title
        if any(p.search(t) for p in self.title_hard_exc):
            return False
        if any(p.search(t) for p in self.title_always):
            return True
        return any(p.search(t) for p in self.title_inc) and not any(p.search(t) for p in self.title_exc)

    def __call__(self, job: Job) -> bool:
        return bool(job.title) and self.title_ok(job) and self.location_ok(job)


def dedupe(jobs: list[Job]) -> list[Job]:
    """Collapse the same posting listed on several boards. VC boards win over the aggregator."""
    best: dict[str, Job] = {}
    for job in sorted(jobs, key=lambda j: j.group != "vc"):
        key = job.dedupe_key
        if key not in best:
            best[key] = job
            continue
        keep = best[key]
        if job.source not in keep.also_on and job.source != keep.source:
            keep.also_on.append(job.source)
        for f in job.funds:
            if f not in keep.funds:
                keep.funds.append(f)
        keep.salary = keep.salary or job.salary
    return list(best.values())
