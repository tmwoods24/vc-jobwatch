"""Google Sheets tracker: the job feed you rate, your application log, and a dashboard.

Tabs
  Jobs          one row per reported job, newest on top. You fill in "My fit", "Applied", "My note".
  Applications  one row per application. Ticking "Applied" on Jobs copies the row here; you can also
                add rows by hand for jobs you found elsewhere. You keep "Status" etc. up to date.
  Dashboard     formulas only (pipeline by status, response rate, upcoming next steps).

Credentials (GitHub secrets / env vars)
  GOOGLE_SERVICE_ACCOUNT_JSON  the service account's JSON key (the whole file's contents)
  SHEET_ID                     the long ID in the sheet's URL: docs.google.com/spreadsheets/d/<SHEET_ID>/edit
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

from .models import Job

log = logging.getLogger(__name__)

JOBS_TAB, APPS_TAB, DASH_TAB = "Jobs", "Applications", "Dashboard"

JOBS_HEADERS = ["Added", "Score", "Title", "Company", "Location", "Pay", "Investors / board",
                "Why it might fit", "Link", "My fit", "Applied", "My note", "Job ID"]
APPS_HEADERS = ["Company", "Role", "Link", "Source", "Applied on", "Status", "Next step",
                "Next step date", "Contact", "Notes", "Job ID"]

FIT_GOOD, FIT_MAYBE, FIT_BAD = "👍 Good fit", "🤔 Maybe", "👎 Not a fit"
FIT_OPTIONS = [FIT_GOOD, FIT_MAYBE, FIT_BAD]
STATUSES = ["Applied", "Recruiter screen", "Interviewing", "Final round", "Offer",
            "Rejected", "Withdrawn", "No response"]
RESPONDED = ["Recruiter screen", "Interviewing", "Final round", "Offer", "Rejected"]

J = {h: i for i, h in enumerate(JOBS_HEADERS)}
A = {h: i for i, h in enumerate(APPS_HEADERS)}
MAX_ROWS = 5000


def col(i: int) -> str:
    """0-based column index -> letter."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _safe(v) -> str:
    """Stop text like '+1 role' or '=foo' being read as a formula."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def sheet_url() -> str | None:
    sid = os.environ.get("SHEET_ID")
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else None


# --------------------------------------------------------------------------- backend
class GSheet:
    """Thin wrapper over gspread so the logic below can be tested with a fake."""

    def __init__(self, spreadsheet):
        self.ss = spreadsheet

    @classmethod
    def from_env(cls) -> "GSheet | None":
        raw, sid = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("SHEET_ID")
        if not (raw and sid):
            return None
        import gspread  # imported lazily so the rest of the tool runs without it
        gc = gspread.service_account_from_dict(json.loads(raw))
        return cls(gc.open_by_key(sid))

    def ws(self, title: str, rows: int = 1000, cols: int = 20):
        import gspread
        try:
            return self.ss.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.ss.add_worksheet(title=title, rows=rows, cols=cols)

    def sheet_id(self, title: str) -> int:
        return self.ws(title).id

    def values(self, title: str) -> list[list[str]]:
        return self.ws(title).get_values()

    def insert_top(self, title: str, rows: list[list]):
        if rows:
            self.ws(title).insert_rows(rows, row=2, value_input_option="USER_ENTERED")

    def append(self, title: str, rows: list[list]):
        if rows:
            self.ws(title).append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")

    def write(self, title: str, a1: str, rows: list[list]):
        self.ws(title).update(rows, a1, value_input_option="USER_ENTERED")

    def batch(self, requests: list[dict]):
        if requests:
            self.ss.batch_update({"requests": requests})


# --------------------------------------------------------------------------- setup
def _validation(sheet_id, c, rule):
    return {"setDataValidation": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": MAX_ROWS,
                  "startColumnIndex": c, "endColumnIndex": c + 1},
        "rule": rule}}


def _list_rule(options):
    return {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in options]},
            "showCustomUi": True, "strict": False}


def _widths(sheet_id, widths):
    return [{"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize"}} for i, w in enumerate(widths)]


def _header(sheet_id, n):
    return [
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": n},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                       "backgroundColor": {"red": .93, "green": .95, "blue": .94}}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}},
    ]


def _color_rule(sheet_id, c, text, rgb):
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": MAX_ROWS,
                    "startColumnIndex": c, "endColumnIndex": c + 1}],
        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                        "format": {"backgroundColor": dict(zip(("red", "green", "blue"), rgb))}}}}}


def validation_requests(gs: GSheet) -> list[dict]:
    """Dropdowns/checkboxes over the full column ranges. Safe to re-apply every run."""
    jid, aid = gs.sheet_id(JOBS_TAB), gs.sheet_id(APPS_TAB)
    return [
        _validation(jid, J["My fit"], _list_rule(FIT_OPTIONS)),
        _validation(jid, J["Applied"], {"condition": {"type": "BOOLEAN"}}),
        _validation(aid, A["Status"], _list_rule(STATUSES)),
        _validation(aid, A["Applied on"], {"condition": {"type": "DATE_IS_VALID"}, "strict": False}),
        _validation(aid, A["Next step date"], {"condition": {"type": "DATE_IS_VALID"}, "strict": False}),
    ]


def dashboard_rows() -> list[list]:
    st = f"{APPS_TAB}!{col(A['Status'])}2:{col(A['Status'])}"
    co = f"{APPS_TAB}!A2:A"
    applied_on = f"{APPS_TAB}!{col(A['Applied on'])}2:{col(A['Applied on'])}"
    fit = f"{JOBS_TAB}!{col(J['My fit'])}2:{col(J['My fit'])}"
    appl = f"{JOBS_TAB}!{col(J['Applied'])}2:{col(J['Applied'])}"
    responded = "+".join(f'COUNTIF({st},"{s}")' for s in RESPONDED)
    rows = [
        ["Pipeline", ""],
        ["Total applications", f"=COUNTA({co})"],
        ["Applied in last 7 days", f"=COUNTIF({applied_on},\">=\"&TODAY()-7)"],
        ["Applied in last 30 days", f"=COUNTIF({applied_on},\">=\"&TODAY()-30)"],
        ["Response rate", f"=IFERROR(({responded})/COUNTA({co}),0)"],
        ["Active (screen → final)", "=" + "+".join(f'COUNTIF({st},"{s}")' for s in ["Recruiter screen", "Interviewing", "Final round"])],
        ["", ""],
        ["By status", "Count"],
    ]
    rows += [[s, f'=COUNTIF({st},"{s}")'] for s in STATUSES]
    rows += [
        ["", ""],
        ["Job feed", ""],
        ["Jobs reported", f"=COUNTA({JOBS_TAB}!A2:A)"],
        ["Rated good fit", f'=COUNTIF({fit},"{FIT_GOOD}")'],
        ["Rated maybe", f'=COUNTIF({fit},"{FIT_MAYBE}")'],
        ["Rated not a fit", f'=COUNTIF({fit},"{FIT_BAD}")'],
        ["Good fits not yet applied", f'=COUNTIFS({fit},"{FIT_GOOD}",{appl},FALSE)'],
    ]
    return rows


def setup(gs: GSheet) -> None:
    """Create tabs, headers, dropdowns, colors and the dashboard. Safe to re-run."""
    for title, headers, widths in [
        (JOBS_TAB, JOBS_HEADERS, [86, 50, 280, 150, 170, 110, 200, 330, 70, 110, 64, 220, 40]),
        (APPS_TAB, APPS_HEADERS, [150, 240, 70, 120, 96, 130, 200, 110, 160, 280, 40]),
    ]:
        ws = gs.ws(title, rows=MAX_ROWS, cols=len(headers))
        if getattr(ws, "row_count", MAX_ROWS) < MAX_ROWS:  # dropdowns cover rows 2..MAX_ROWS
            ws.resize(rows=MAX_ROWS)
        current = gs.values(title)
        if not current or current[0][:len(headers)] != headers:
            gs.write(title, "A1", [headers])
        sid = ws.id
        req = _header(sid, len(headers)) + _widths(sid, widths)
        req.append({"updateDimensionProperties": {  # hide the Job ID column
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": len(headers) - 1, "endIndex": len(headers)},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
        gs.batch(req)

    jid, aid = gs.sheet_id(JOBS_TAB), gs.sheet_id(APPS_TAB)
    rules = [
        _color_rule(jid, J["My fit"], FIT_GOOD, (.85, .94, .86)),
        _color_rule(jid, J["My fit"], FIT_BAD, (.95, .87, .87)),
        _color_rule(aid, A["Status"], "Offer", (.78, .92, .80)),
        _color_rule(aid, A["Status"], "Final round", (.86, .94, .87)),
        _color_rule(aid, A["Status"], "Interviewing", (.90, .95, .90)),
        _color_rule(aid, A["Status"], "Recruiter screen", (.93, .96, .99)),
        _color_rule(aid, A["Status"], "Rejected", (.93, .93, .93)),
        _color_rule(aid, A["Status"], "No response", (.93, .93, .93)),
        _color_rule(aid, A["Status"], "Withdrawn", (.93, .93, .93)),
    ]
    gs.batch(validation_requests(gs) + rules)

    gs.ws(DASH_TAB, rows=60, cols=6)
    gs.write(DASH_TAB, "A1", dashboard_rows())
    did = gs.sheet_id(DASH_TAB)
    upcoming = (f'=IFERROR(SORT(FILTER({{{APPS_TAB}!A2:A,{APPS_TAB}!B2:B,{APPS_TAB}!G2:G,{APPS_TAB}!H2:H}},'
                f'{APPS_TAB}!H2:H<>""),4,TRUE),"No next steps logged")')
    gs.write(DASH_TAB, "D1", [["Upcoming next steps", "", "", ""], ["Company", "Role", "Next step", "Date"], [upcoming]])
    gs.batch(_widths(did, [220, 90, 24, 160, 240, 200]) + [
        {"repeatCell": {"range": {"sheetId": did, "startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 1, "endColumnIndex": 2},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
    ] + [{"repeatCell": {"range": {"sheetId": did, "startRowIndex": r, "endRowIndex": r + 1, "startColumnIndex": c0, "endColumnIndex": c1},
                         "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat"}}
         for r, c0, c1 in [(0, 0, 2), (7, 0, 2), (17, 0, 2), (0, 3, 7), (1, 3, 7)]])
    _drop_default_sheet(gs)
    log.info("Sheet set up: %s, %s, %s", JOBS_TAB, APPS_TAB, DASH_TAB)


def _drop_default_sheet(gs: GSheet):
    try:
        for w in gs.ss.worksheets():
            if w.title in ("Sheet1",) and len(gs.ss.worksheets()) > 1 and not any(w.get_values()):
                gs.ss.del_worksheet(w)
    except Exception:  # cosmetic only
        pass


# --------------------------------------------------------------------------- weekly sync
def job_count(gs: GSheet) -> int:
    """Rows that hold a job. Rows below the data can contain unchecked checkboxes (FALSE), so
    count by Title rather than by raw row count."""
    return sum(1 for r in gs.values(JOBS_TAB)[1:] if len(r) > J["Title"] and r[J["Title"]].strip())


def job_row(j: Job, today: str) -> list:
    boards = [j.source] + j.also_on
    investors = [f for f in j.funds if f not in boards][:5]
    inv = ", ".join(boards) + (f" (investors: {', '.join(investors)})" if investors else "")
    return [today, j.score if j.score is not None else "", _safe(j.title), _safe(j.company),
            _safe(j.location_text()), _safe(j.salary), _safe(inv), _safe(j.reason), j.url, "", False, "", j.uid]


def add_jobs(gs: GSheet, jobs: list[Job], today: str, added: dict[str, str] | None = None) -> int:
    """Insert new jobs at the top of the Jobs tab, best score first. Skips jobs already present.
    `added` optionally maps job uid -> the date it was first reported (used when backfilling)."""
    existing = {r[J["Job ID"]] for r in gs.values(JOBS_TAB)[1:] if len(r) > J["Job ID"]}
    fresh = [j for j in jobs if j.uid not in existing]
    fresh.sort(key=lambda j: -(j.score or 0))
    added = added or {}
    gs.insert_top(JOBS_TAB, [job_row(j, added.get(j.uid, today)) for j in fresh])
    if fresh:
        gs.batch(validation_requests(gs))  # make sure new rows carry the dropdown/checkbox
    return len(fresh)


def _truthy(v: str) -> bool:
    return str(v).strip().upper() in ("TRUE", "YES", "Y", "X", "✓", "✔")


def sync_applied(gs: GSheet, today: str) -> int:
    """Copy jobs ticked "Applied" on the Jobs tab into Applications (once each)."""
    jobs = gs.values(JOBS_TAB)[1:]
    apps = gs.values(APPS_TAB)[1:]
    have_ids = {r[A["Job ID"]] for r in apps if len(r) > A["Job ID"] and r[A["Job ID"]]}
    have_links = {r[A["Link"]] for r in apps if len(r) > A["Link"] and r[A["Link"]]}
    new_rows = []
    for r in jobs:
        r = r + [""] * (len(JOBS_HEADERS) - len(r))
        if not _truthy(r[J["Applied"]]):
            continue
        jid, link = r[J["Job ID"]], r[J["Link"]]
        if (jid and jid in have_ids) or (link and link in have_links):
            continue
        source = (r[J["Investors / board"]] or "").split(" (")[0].split(",")[0]
        row = [""] * len(APPS_HEADERS)
        row[A["Company"]], row[A["Role"]], row[A["Link"]] = _safe(r[J["Company"]]), _safe(r[J["Title"]]), link
        row[A["Source"]], row[A["Applied on"]], row[A["Status"]] = _safe(source), today, "Applied"
        row[A["Notes"]], row[A["Job ID"]] = _safe(r[J["My note"]]), jid
        new_rows.append(row)
        have_ids.add(jid)
    gs.append(APPS_TAB, new_rows)
    return len(new_rows)


# --------------------------------------------------------------------------- learning
def read_feedback(gs: GSheet) -> dict:
    """Your ratings, notes and applications, for the scorer to learn from."""
    out = {"good": [], "maybe": [], "bad": [], "applied_manual": []}
    rows = gs.values(JOBS_TAB)[1:]
    job_ids = set()
    for r in rows:  # newest first
        r = r + [""] * (len(JOBS_HEADERS) - len(r))
        job_ids.add(r[J["Job ID"]])
        desc = f'{r[J["Title"]]} | {r[J["Company"]]} | {r[J["Location"]]}'
        if r[J["Investors / board"]]:
            desc += f' | {r[J["Investors / board"]][:80]}'
        if r[J["My note"]].strip():
            desc += f' | my note: {r[J["My note"]].strip()}'
        fit = r[J["My fit"]]
        if fit == FIT_BAD:
            out["bad"].append(desc)
        elif fit == FIT_GOOD or _truthy(r[J["Applied"]]):
            out["good"].append(desc + (" | I applied" if _truthy(r[J["Applied"]]) else ""))
        elif fit == FIT_MAYBE:
            out["maybe"].append(desc)
    for r in gs.values(APPS_TAB)[1:]:  # applications you logged yourself
        r = r + [""] * (len(APPS_HEADERS) - len(r))
        if r[A["Company"]] and r[A["Job ID"]] not in job_ids:
            d = f'{r[A["Role"]]} | {r[A["Company"]]}'
            if r[A["Notes"]].strip():
                d += f' | my note: {r[A["Notes"]].strip()[:120]}'
            out["applied_manual"].append(d)
    return out


def feedback_prompt(fb: dict, limit: int = 40) -> str:
    """Turn ratings into few-shot guidance for the scorer. Empty string when there are none."""
    parts = []
    for key, label in [("good", "Jobs I marked as GOOD fits (or applied to)"),
                       ("applied_manual", "Jobs I found and applied to on my own"),
                       ("maybe", "Jobs I marked MAYBE"),
                       ("bad", "Jobs I marked NOT a fit")]:
        items = fb.get(key, [])[:limit]
        if items:
            parts.append(f"{label}:\n" + "\n".join(f"- {x}" for x in items))
    if not parts:
        return ""
    return ("These are my own judgments on past postings. They outrank the profile when the two disagree; "
            "learn the pattern (role type, seniority, company stage, sector, location) and any reasons in my notes.\n\n"
            + "\n\n".join(parts))


def feedback_counts(fb: dict) -> dict:
    return {k: len(v) for k, v in fb.items()}


def today_str() -> str:
    return dt.date.today().isoformat()
