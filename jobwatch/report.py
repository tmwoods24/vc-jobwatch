"""Builds the weekly report as email-safe HTML (inline styles, table layout) and plain text."""
from __future__ import annotations

import datetime as dt
import html
import re

from .models import Job

INVESTING = re.compile(r"\bventure|\binvestment (associate|analyst|manager|principal|team)|\binvesting\b|\binvestor\b(?! relations)|portfolio (operations|support|talent|success)|\bplatform (associate|manager|lead|director)", re.I)

INK = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
ACCENT = "#0b6e4f"


def _e(s) -> str:
    return html.escape(str(s or ""))


def _score_badge(score: int | None) -> str:
    if score is None:
        return ""
    bg = "#0b6e4f" if score >= 8 else "#3a7d5c" if score >= 6 else "#8e8e93"
    return (f'<span style="display:inline-block;min-width:22px;text-align:center;padding:2px 6px;'
            f'border-radius:10px;background:{bg};color:#fff;font-size:12px;font-weight:600">{score}</span>')


def _job_row(j: Job) -> str:
    meta = [j.company, j.location_text()]
    if j.salary:
        meta.append(j.salary)
    if j.posted:
        meta.append(f"posted {j.posted}")
    boards = [j.source] + j.also_on
    funds = [f for f in j.funds if f not in boards][:4]
    via = "via " + ", ".join(boards) + (f" · investors: {', '.join(funds)}" if funds and j.group == "aggregator" else "")
    reason = f'<div style="color:{INK};font-size:13px;margin-top:3px">{_e(j.reason)}</div>' if j.reason else ""
    return f"""
<tr><td style="padding:10px 0;border-bottom:1px solid {LINE};vertical-align:top;width:34px">{_score_badge(j.score)}</td>
<td style="padding:10px 0 10px 8px;border-bottom:1px solid {LINE}">
  <a href="{_e(j.url)}" style="color:{ACCENT};font-weight:600;font-size:15px;text-decoration:none">{_e(j.title)}</a>
  <div style="color:{MUTED};font-size:13px;margin-top:2px">{_e(' · '.join(meta))}</div>
  {reason}
  <div style="color:{MUTED};font-size:12px;margin-top:2px">{_e(via)}</div>
</td></tr>"""


def _section(title: str, jobs: list[Job], note: str = "") -> str:
    if not jobs:
        return ""
    rows = "".join(_job_row(j) for j in jobs)
    note_html = f'<div style="color:{MUTED};font-size:13px;margin:0 0 6px">{_e(note)}</div>' if note else ""
    return f"""
<h2 style="font-size:17px;color:{INK};margin:28px 0 4px">{_e(title)} <span style="color:{MUTED};font-weight:400">({len(jobs)})</span></h2>
{note_html}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{rows}</table>"""


def _sort(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda j: (-(j.score or 0), j.group != "vc", j.posted or ""), reverse=False)


def bucket(jobs: list[Job], min_score: int) -> dict[str, list[Job]]:
    scored = any(j.score is not None for j in jobs)
    investing = [j for j in jobs if INVESTING.search(j.title) and (j.score or 10) >= min_score]
    rest = [j for j in jobs if j not in investing]
    if not scored:
        return {"investing": _sort(investing), "top": _sort(rest), "maybe": [], "low": []}
    return {
        "investing": _sort(investing),
        "top": _sort([j for j in rest if (j.score or 0) >= 7]),
        "maybe": _sort([j for j in rest if min_score <= (j.score or 0) < 7]),
        "low": _sort([j for j in rest if (j.score or 0) < min_score]),
    }


def build_html(jobs: list[Job], health: list[dict], cfg: dict, run_date: str | None = None) -> str:
    run_date = run_date or dt.date.today().isoformat()
    min_score = cfg.get("scoring", {}).get("min_score_in_report", 4)
    b = bucket(jobs, min_score)
    scored = any(j.score is not None for j in jobs)
    title = cfg.get("report", {}).get("title", "Weekly NYC role watch")

    body = ""
    body += _section("Investing & platform roles", b["investing"], "Titles that mention venture, investing, portfolio or platform.")
    body += _section("Top matches" if scored else "New matching roles", b["top"])
    body += _section("Worth a look", b["maybe"])
    if b["low"]:
        items = "".join(
            f'<li style="margin:2px 0"><a href="{_e(j.url)}" style="color:{MUTED}">{_e(j.title)}</a> '
            f'<span style="color:{MUTED}">· {_e(j.company)} ({j.score})</span></li>' for j in b["low"])
        body += (f'<h2 style="font-size:15px;color:{MUTED};margin:28px 0 4px">Low fit ({len(b["low"])})</h2>'
                 f'<ul style="font-size:13px;padding-left:18px;margin:0">{items}</ul>')
    if not jobs:
        body = f'<p style="color:{MUTED};font-size:15px">No new matching roles this week.</p>'

    failed = [h for h in health if h.get("error")]
    ok = [h for h in health if not h.get("error")]
    health_rows = "".join(
        f'<tr><td style="padding:2px 12px 2px 0">{_e(h["name"])}</td>'
        f'<td style="padding:2px 12px 2px 0;text-align:right">{h.get("fetched", 0):,}</td>'
        f'<td style="padding:2px 0;text-align:right">{h.get("new", 0)}</td></tr>' for h in ok)
    fail_html = ""
    if failed:
        fail_html = (f'<p style="color:#b3261e;font-size:13px;margin:8px 0 0"><b>Could not read:</b> '
                     + "; ".join(f'{_e(h["name"])} ({_e(h["error"])[:120]})' for h in failed) + "</p>")

    summary = (f"{len(jobs)} new role{'s' if len(jobs) != 1 else ''} since last week across "
               f"{len(ok)} board{'s' if len(ok) != 1 else ''}")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}: {_e(run_date)}</title></head>
<body style="margin:0;padding:0;background:#f5f5f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;background:#ffffff;border-radius:12px">
<tr><td style="padding:28px 28px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:{INK}">
<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{MUTED}">{_e(run_date)}</div>
<h1 style="font-size:22px;margin:4px 0 6px">{_e(title)}</h1>
<div style="font-size:14px;color:{MUTED}">{_e(summary)}{' · ranked by Claude fit score' if scored else ''}</div>
{body}
<h2 style="font-size:14px;color:{MUTED};margin:32px 0 6px">Boards checked</h2>
<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:12px;color:{MUTED}">
<tr><td style="padding:2px 12px 2px 0"><b>Board</b></td><td style="padding:2px 12px 2px 0;text-align:right"><b>Jobs pulled</b></td><td style="text-align:right"><b>New matches</b></td></tr>
{health_rows}</table>{fail_html}
</td></tr></table></td></tr></table></body></html>"""


def build_text(jobs: list[Job], cfg: dict) -> str:
    min_score = cfg.get("scoring", {}).get("min_score_in_report", 4)
    b = bucket(jobs, min_score)
    out = []
    for name, key in [("INVESTING & PLATFORM", "investing"), ("TOP MATCHES", "top"), ("WORTH A LOOK", "maybe")]:
        if b[key]:
            out.append(f"\n== {name} ==")
            for j in b[key]:
                s = f"[{j.score}] " if j.score is not None else ""
                out.append(f"{s}{j.title} | {j.company} | {j.location_text()}\n    {j.url}")
                if j.reason:
                    out.append(f"    {j.reason}")
    return "\n".join(out) or "No new matching roles this week."
