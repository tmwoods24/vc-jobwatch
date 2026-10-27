"""vc-jobwatch: weekly digest of new NYC roles across VC portfolio job boards.

  python -m jobwatch run                 # fetch, filter, score, save report, email, remember
  python -m jobwatch run --no-email      # same, but only write reports/<date>.html
  python -m jobwatch run --dry-run       # don't update data/seen.json (safe for testing)
  python -m jobwatch run --only FirstMark --only "Lux Capital"
  python -m jobwatch run --all           # ignore seen.json and report every current match
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import yaml

from .emailer import send_report
from .filters import JobFilter, dedupe
from .http import Throttle, make_session
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
    by_src = {}
    for j in new:
        by_src[j.source] = by_src.get(j.source, 0) + 1
    for h in health:
        h["new"] = by_src.get(h["name"], 0)
    log.info("%d matching roles, %d new", len(matched), len(new))

    if new and cfg.get("scoring", {}).get("enabled") and not args.no_score:
        profile_path = ROOT / cfg["scoring"].get("profile_file", "profile.md")
        score_jobs(new, cfg, profile_path.read_text() if profile_path.exists() else "")

    html_out = build_html(new, health, cfg, today)
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
        store.mark(new, today)
        store.prune(today)
        store.save()

    if all(h.get("error") for h in health):
        return 1  # every board failed: make the scheduled run show as failed
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
    r.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
