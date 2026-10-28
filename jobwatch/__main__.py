"""vc-jobwatch: weekly digest of new NYC roles across VC portfolio job boards.

  python -m jobwatch run                 # fetch, filter, score, save report, email, remember
  python -m jobwatch run --no-email      # same, but only write reports/<date>.html
  python -m jobwatch run --dry-run       # don't update data/seen.json (safe for testing)
  python -m jobwatch run --only FirstMark --only "Lux Capital"
  python -m jobwatch run --all           # ignore seen.json and report every current match
  python -m jobwatch sync [--setup]      # copy "Applied" ticks to the Applications tab (daily job)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import yaml

from . import sheets
from .emailer import send_report
from .filters import JobFilter, dedupe
from .http import Throttle, make_session
from .models import Job
from .report import build_html, build_text
from .scoring import score_jobs
from .sources import FETCHERS
from .store import SeenStore

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("jobwatch")


def run(args) -> int:
    cfg = yaml.safe_load((ROOT / args.config).read_text())
    today = dt.date.today().isoformat()
    session = make_session()
    jf = JobFilter(cfg)
    store = SeenStore(ROOT / "data" / "seen.json", cfg.get("seen_retention_days", 180))

    sources = [s for s in cfg["sources"] if not args.only or s["name"] in args.only]
    health, matched = [], []
    for src in sources:
        fetch = FETCHERS.get(src["type"])
        if not fetch:
            health.append({"name": src["name"], "error": f"unknown type {src['type']}"})
            continue
        try:
            jobs = fetch(src, session, Throttle(cfg.get("request_delay", 1.0)))
        except Exception as e:
            log.error("%s failed: %s", src["name"], e)
            health.append({"name": src["name"], "error": str(e)})
            continue
        hits = [j for j in jobs if jf(j)]
        health.append({"name": src["name"], "fetched": len(jobs), "matched": len(hits)})
        matched.extend(hits)

    matched = dedupe(matched)
    new = matched if args.all else [j for j in matched if store.is_new(j)]

    # First run (empty history): only report jobs posted on/after `first_run_since`.
    # Older matches are still remembered below, so they never show up in later weeks.
    since = args.since or (str(cfg.get("first_run_since") or "") if store.is_empty() else "")
    period = "since last week"
    if since:
        new = [j for j in new if not j.posted or j.posted >= since]
        d0 = dt.date.fromisoformat(since)
        period = f"posted since {d0:%b} {d0.day}"
        log.info("Limiting report to jobs posted on/after %s", since)
    by_src = {}
    for j in new:
        by_src[j.source] = by_src.get(j.source, 0) + 1
    for h in health:
        h["new"] = by_src.get(h["name"], 0)
    log.info("%d matching roles, %d new", len(matched), len(new))

    # Tracker: pick up your "Applied" ticks and ratings before scoring, so the scorer learns from them.
    gs, feedback, fb_counts = None, "", {}
    if not args.no_sheet:
        try:
            gs = sheets.GSheet.from_env()
            if gs:
                ensure_setup(gs)
                moved = sheets.sync_applied(gs, today)
                fb = sheets.read_feedback(gs)
                feedback, fb_counts = sheets.feedback_prompt(fb), sheets.feedback_counts(fb)
                log.info("Tracker: %d newly applied copied; learning from %s", moved, fb_counts)
        except Exception as e:
            log.error("Google Sheet unavailable: %s", e)
            gs = None

    if new and cfg.get("scoring", {}).get("enabled") and not args.no_score:
        profile_path = ROOT / cfg["scoring"].get("profile_file", "profile.md")
        score_jobs(new, cfg, profile_path.read_text() if profile_path.exists() else "", feedback)

    if gs and not args.dry_run:
        try:
            backfill_sheet(gs, cfg)
            added = sheets.add_jobs(gs, new, today)
            log.info("Tracker: added %d jobs to the Jobs tab", added)
        except Exception as e:
            log.error("Could not add jobs to the sheet: %s", e)

    html_out = build_html(new, health, cfg, today, period=period,
                          sheet_url=sheets.sheet_url() if gs else None, learning=fb_counts)
    text_out = build_text(new, cfg)
    out_dir = ROOT / cfg.get("report", {}).get("output_dir", "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{today}.html").write_text(html_out)
    (out_dir / "latest.html").write_text(html_out)
    (out_dir / f"{today}.json").write_text(json.dumps([j.to_dict() for j in new], indent=1))
    log.info("Report written to %s", out_dir / f"{today}.html")

    if not args.no_email:
        top = sum(1 for j in new if (j.score or 0) >= 7)
        subject = f"{cfg.get('report', {}).get('title', 'Job watch')}: {len(new)} new" + (f", {top} strong" if top else "")
        try:
            send_report(subject, html_out, text_out)
        except Exception as e:
            log.error("Email failed: %s", e)

    if not args.dry_run:
        store.touch(matched, today)
        store.mark(matched if since else new, today)
        store.prune(today)
        store.save()

    if all(h.get("error") for h in health):
        return 1  # every board failed: make the scheduled run show as failed
    return 0


def ensure_setup(gs) -> None:
    rows = gs.values(sheets.JOBS_TAB)
    if not rows or rows[0][:3] != sheets.JOBS_HEADERS[:3]:
        sheets.setup(gs)


def backfill_sheet(gs, cfg: dict | None = None) -> None:
    """If the Jobs tab has no jobs yet, load earlier reports (reports/<date>.json) so past jobs can be
    rated too. Merges every report (keeping the scored copy of each job and the date it was first
    reported), honors `first_run_since`, and scores leftovers when an API key is set."""
    if sheets.job_count(gs) > 0:
        return
    cfg = cfg or {}
    since = str(cfg.get("first_run_since") or "")
    by_uid: dict[str, Job] = {}
    first_seen: dict[str, str] = {}
    for path in sorted((ROOT / "reports").glob("20*.json")):
        for d in json.loads(path.read_text()):
            job = Job(**d)
            first_seen.setdefault(job.uid, path.stem)
            if job.uid not in by_uid or job.score is not None:
                by_uid[job.uid] = job
    jobs = [j for j in by_uid.values() if not since or not j.posted or j.posted >= since]
    if not jobs:
        return
    unscored = [j for j in jobs if j.score is None]
    if unscored and cfg.get("scoring", {}).get("enabled"):
        profile_path = ROOT / cfg["scoring"].get("profile_file", "profile.md")
        score_cfg = dict(cfg, scoring=dict(cfg["scoring"], max_jobs=len(unscored)))
        log.info("Tracker: scoring %d previously unscored jobs", len(unscored))
        score_jobs(unscored, score_cfg, profile_path.read_text() if profile_path.exists() else "")
    n = sheets.add_jobs(gs, jobs, today_iso(), added=first_seen)
    log.info("Tracker: backfilled %d jobs from %d earlier report(s)", n, len(set(first_seen.values())))


def today_iso() -> str:
    return dt.date.today().isoformat()


def sync_cmd(args) -> int:
    """Daily: copy ticked jobs into Applications. No fetching, no email."""
    gs = sheets.GSheet.from_env()
    if not gs:
        log.error("Set GOOGLE_SERVICE_ACCOUNT_JSON and SHEET_ID")
        return 1
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    ensure_setup(gs)
    if args.setup:
        sheets.setup(gs)
    backfill_sheet(gs, cfg)
    n = sheets.sync_applied(gs, dt.date.today().isoformat())
    log.info("Copied %d newly applied jobs to Applications", n)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", default="config.yaml")
    r.add_argument("--no-email", action="store_true")
    r.add_argument("--no-score", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="don't update data/seen.json")
    r.add_argument("--all", action="store_true", help="report every current match, not just new ones")
    r.add_argument("--only", action="append", help="limit to a source name (repeatable)")
    r.add_argument("--since", help="only report jobs posted on/after this date (YYYY-MM-DD)")
    r.add_argument("--no-sheet", action="store_true", help="skip the Google Sheet tracker")
    r.add_argument("-v", "--verbose", action="store_true")
    sy = sub.add_parser("sync", help="copy jobs ticked 'Applied' into the Applications tab")
    sy.add_argument("--setup", action="store_true", help="(re)create tabs, dropdowns and dashboard first")
    sy.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    return run(args) if args.cmd == "run" else sync_cmd(args)


if __name__ == "__main__":
    sys.exit(main())
