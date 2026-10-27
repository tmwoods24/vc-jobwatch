from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict


@dataclass
class Job:
    """One job posting, normalized across every source."""

    source: str                 # board name from config, e.g. "FirstMark"
    group: str                  # "vc" or "aggregator"
    external_id: str            # the board's own ID for the job
    title: str
    company: str
    url: str
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    posted: str = ""            # ISO date (YYYY-MM-DD) when known
    salary: str = ""
    funds: list[str] = field(default_factory=list)   # investors, when the source lists them
    tags: list[str] = field(default_factory=list)    # seniority, stage, industry, etc.

    # Filled in later in the pipeline
    score: int | None = None
    reason: str = ""
    also_on: list[str] = field(default_factory=list)  # other boards listing the same job

    @property
    def uid(self) -> str:
        """Stable ID used to remember which jobs we've already reported."""
        return f"{self.source}:{self.external_id}"

    @property
    def dedupe_key(self) -> str:
        """Same company + same title + same city counts as one job across boards."""
        loc = self.locations[0] if self.locations else ""
        raw = f"{_norm(self.company)}|{_norm(self.title)}|{_norm(loc.split(',')[0])}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def location_text(self) -> str:
        locs = "; ".join(self.locations) if self.locations else "Location not listed"
        return f"{locs} (remote)" if self.remote and "remote" not in locs.lower() else locs

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def format_salary(lo, hi, currency="USD", period="year", cents=False) -> str:
    if not lo and not hi:
        return ""
    div = 100 if cents else 1

    def k(v):
        if v is None:
            return None
        v = v / div
        return f"{v/1000:.0f}k" if v >= 1000 else f"{v:.0f}"

    lo_s, hi_s = k(lo), k(hi)
    rng = f"{lo_s}-{hi_s}" if lo_s and hi_s and lo_s != hi_s else (lo_s or hi_s)
    sym = "$" if (currency or "USD").upper() == "USD" else f"{currency} "
    per = {"year": "/yr", "hour": "/hr", "month": "/mo"}.get(period or "", "")
    return f"{sym}{rng}{per}"
