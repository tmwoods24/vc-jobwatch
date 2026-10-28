"""Remembers which jobs have already been reported (data/seen.json, committed back to the repo)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .models import Job


class SeenStore:
    def __init__(self, path: str | Path, retention_days: int = 180):
        self.path = Path(path)
        self.retention = retention_days
        self.data: dict[str, dict] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text() or "{}")

    def is_empty(self) -> bool:
        return not self.data

    def is_new(self, job: Job) -> bool:
        # A job counts as seen if either its board ID or its cross-board key was reported before.
        return job.uid not in self.data and f"key:{job.dedupe_key}" not in self.data

    def mark(self, jobs: list[Job], today: str | None = None):
        today = today or dt.date.today().isoformat()
        for j in jobs:
            for k in (j.uid, f"key:{j.dedupe_key}"):
                rec = self.data.setdefault(k, {"first_seen": today})
                rec["last_seen"] = today

    def touch(self, jobs: list[Job], today: str | None = None):
        """Refresh last_seen for jobs still live, so they aren't pruned and re-reported."""
        today = today or dt.date.today().isoformat()
        for j in jobs:
            for k in (j.uid, f"key:{j.dedupe_key}"):
                if k in self.data:
                    self.data[k]["last_seen"] = today

    def prune(self, today: str | None = None):
        cutoff = (dt.date.fromisoformat(today) if today else dt.date.today()) - dt.timedelta(days=self.retention)
        self.data = {k: v for k, v in self.data.items()
                     if dt.date.fromisoformat(v.get("last_seen", v["first_seen"])) >= cutoff}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=0, sort_keys=True))
