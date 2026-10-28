"""Tracker logic against an in-memory fake of the Google Sheet."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobwatch import sheets  # noqa: E402
from jobwatch.models import Job  # noqa: E402
from jobwatch.sheets import A, J  # noqa: E402


class FakeWS:
    def __init__(self, title, sid):
        self.title, self.id, self.rows = title, sid, []


class FakeGSheet(sheets.GSheet):
    """Implements the GSheet surface with lists. Cells come back as strings, like the real API."""

    def __init__(self):
        self.tabs, self.requests = {}, []

    def ws(self, title, rows=1000, cols=20):
        if title not in self.tabs:
            self.tabs[title] = FakeWS(title, len(self.tabs) + 1)
        return self.tabs[title]

    def values(self, title):
        return [[("TRUE" if v is True else "FALSE" if v is False else str(v)) for v in r] for r in self.ws(title).rows]

    def insert_top(self, title, rows):
        ws = self.ws(title)
        ws.rows[1:1] = [list(r) for r in rows]

    def append(self, title, rows):
        self.ws(title).rows.extend(list(r) for r in rows)

    def write(self, title, a1, rows):
        ws = self.ws(title)
        if a1 == "A1":
            for i, r in enumerate(rows):
                while len(ws.rows) <= i:
                    ws.rows.append([])
                ws.rows[i] = list(r) + ws.rows[i][len(r):]

    def batch(self, requests):
        self.requests.extend(requests)


def job(i, title="Chief of Staff", company="Acme", score=8):
    return Job(source="FirstMark", group="vc", external_id=str(i), title=title, company=company,
               url=f"https://ats/{i}", locations=["New York, NY"], score=score, reason="Strategic CoS")


def set_cell(gs, tab, row_idx, col_idx, value):
    r = gs.tabs[tab].rows[row_idx]
    while len(r) <= col_idx:
        r.append("")
    r[col_idx] = value


def test_setup_and_add_jobs_newest_on_top():
    gs = FakeGSheet()
    sheets.setup(gs)
    assert gs.values("Jobs")[0] == sheets.JOBS_HEADERS
    assert gs.values("Applications")[0] == sheets.APPS_HEADERS
    assert any("setDataValidation" in r for r in gs.requests)
    assert gs.values("Dashboard")[1][0] == "Total applications"

    assert sheets.add_jobs(gs, [job(1, score=6), job(2, "Strategic Finance Lead", score=9)], "2026-10-27") == 2
    assert sheets.add_jobs(gs, [job(1), job(3, "Venture Associate")], "2026-11-02") == 1   # job 1 not duplicated
    titles = [r[J["Title"]] for r in gs.values("Jobs")[1:]]
    assert titles == ["Venture Associate", "Strategic Finance Lead", "Chief of Staff"]


def test_applied_tick_copies_once_and_manual_rows_survive():
    gs = FakeGSheet()
    sheets.setup(gs)
    sheets.add_jobs(gs, [job(1), job(2, "Head of Strategy", "Beta")], "2026-10-27")
    gs.append("Applications", [["Gamma", "Chief of Staff", "https://gamma/jobs/1", "Referral", "2026-10-20",
                                "Interviewing", "Final with CEO", "2026-11-04", "Jane", "Met at Booth event", ""]])
    set_cell(gs, "Jobs", 2, J["Applied"], True)          # tick the Acme job (row 2: inserted first, lower score order)
    set_cell(gs, "Jobs", 2, J["My note"], "Applied via referral")
    assert sheets.sync_applied(gs, "2026-10-28") == 1
    assert sheets.sync_applied(gs, "2026-10-29") == 0     # idempotent
    apps = gs.values("Applications")[1:]
    assert [a[A["Company"]] for a in apps] == ["Gamma", gs.values("Jobs")[2][J["Company"]]]
    new = apps[1]
    assert new[A["Status"]] == "Applied" and new[A["Applied on"]] == "2026-10-28"
    assert new[A["Notes"]] == "Applied via referral" and new[A["Source"]] == "FirstMark"


def test_feedback_reads_ratings_notes_and_manual_applications():
    gs = FakeGSheet()
    sheets.setup(gs)
    sheets.add_jobs(gs, [job(1, "Chief of Staff", "Kinstead"), job(2, "FP&A Analyst", "Horizon3", 3),
                         job(3, "Strategy & Ops Lead", "Alchemi", 7)], "2026-10-27")
    rows = gs.values("Jobs")
    idx = {r[J["Company"]]: i for i, r in enumerate(rows)}
    set_cell(gs, "Jobs", idx["Kinstead"], J["My fit"], sheets.FIT_GOOD)
    set_cell(gs, "Jobs", idx["Horizon3"], J["My fit"], sheets.FIT_BAD)
    set_cell(gs, "Jobs", idx["Horizon3"], J["My note"], "too junior")
    set_cell(gs, "Jobs", idx["Alchemi"], J["Applied"], True)
    gs.append("Applications", [["Lowercarbon", "Investment Associate", "", "Network", "2026-10-15", "Applied",
                                "", "", "", "Dream fund", ""]])
    fb = sheets.read_feedback(gs)
    assert sheets.feedback_counts(fb) == {"good": 2, "maybe": 0, "bad": 1, "applied_manual": 1}
    text = sheets.feedback_prompt(fb)
    assert "Kinstead" in text and "too junior" in text and "I applied" in text and "Dream fund" in text
    assert sheets.feedback_prompt({"good": [], "bad": []}) == ""


def test_formula_injection_is_escaped():
    row = sheets.job_row(job(9, "=IMPORTXML(\"x\")", "+Plus Co"), "2026-10-27")
    assert row[J["Title"]].startswith("'=") and row[J["Company"]].startswith("'+")


def test_backfill_honors_first_run_since(tmp_path, monkeypatch):
    import json
    import jobwatch.__main__ as m
    (tmp_path / "reports").mkdir()
    old = job(1, "Chief of Staff", "Old Co").to_dict() | {"posted": "2026-09-20"}
    new = job(2, "Strategic Finance Lead", "New Co").to_dict() | {"posted": "2026-10-05", "score": None}
    (tmp_path / "reports" / "2026-10-27.json").write_text(json.dumps([old, new]))
    monkeypatch.setattr(m, "ROOT", tmp_path)
    scored = []
    monkeypatch.setattr(m, "score_jobs", lambda jobs, cfg, prof, fb="": scored.extend(j.company for j in jobs))
    gs = FakeGSheet()
    sheets.setup(gs)
    m.backfill_sheet(gs, {"first_run_since": "2026-10-01", "scoring": {"enabled": True}})
    assert [r[J["Company"]] for r in gs.values("Jobs")[1:]] == ["New Co"]
    assert scored == ["New Co"]                      # unscored job got scored during backfill
    m.backfill_sheet(gs, {"first_run_since": "2026-10-01"})   # no-op once the tab has rows
    assert len(gs.values("Jobs")) == 2
