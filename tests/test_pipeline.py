"""Offline tests. Fixtures mirror real response shapes captured from each platform in Oct 2026."""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobwatch.filters import JobFilter, dedupe  # noqa: E402
from jobwatch.http import Throttle  # noqa: E402
from jobwatch.report import build_html, bucket  # noqa: E402
from jobwatch.scoring import apply_scores  # noqa: E402
from jobwatch.sources.consider import fetch_consider  # noqa: E402
from jobwatch.sources.getro import fetch_getro  # noqa: E402
from jobwatch.sources.lowercarbon import fetch_lowercarbon  # noqa: E402
from jobwatch.sources.traction import fetch_traction  # noqa: E402
from jobwatch.store import SeenStore  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yaml").read_text())


class Resp:
    def __init__(self, data=None, text="", status=200):
        self._data, self.text, self.status_code = data, text, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.handler("GET", url, kw)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.handler("POST", url, kw)


def getro_job(i, title, loc, org="Acme", work_mode="on_site"):
    return {"id": i, "title": title, "slug": f"{i}-x", "created_at": 1792759154, "work_mode": work_mode,
            "locations": [loc], "location_details": [{"name": loc}], "compensation_public": True,
            "compensation_amount_min_cents": 16110000, "compensation_amount_max_cents": 20200000,
            "compensation_currency": "USD", "compensation_period": "year", "seniority": "director",
            "organization": {"name": org, "slug": org.lower(), "stage": "series_b"}}


def test_getro_discovers_id_and_paginates():
    pages = [[getro_job(i, "Chief of Staff", "New York, NY, USA") for i in range(20)],
             [getro_job(100, "Strategic Finance Lead", "Remote", work_mode="remote")]]

    def h(method, url, kw):
        if method == "GET":
            return Resp(text='<script>window.__GETRO_BOARD_POLICY={"network_id":"45303","base_path":"/"};</script>')
        assert url == "https://api.getro.com/api/v2/collections/45303/search/jobs"
        assert kw["headers"]["Accept"] == "application/json"
        body = kw["json"]
        assert body["hitsPerPage"] == 20 and body["filters"]["page"] == body["page"]
        assert "New York, NY, USA" in body["filters"]["searchable_locations"]
        return Resp({"results": {"jobs": pages[body["page"]], "count": 21}})

    s = FakeSession(h)
    jobs = fetch_getro({"name": "FirstMark", "type": "getro", "domain": "jobs.firstmark.com"}, s, Throttle(0))
    assert len(jobs) == 21
    j = jobs[0]
    assert j.url == "https://jobs.firstmark.com/companies/acme/jobs/0-x"
    assert j.salary == "$161k-202k/yr" and j.posted == "2026-10-23"
    assert jobs[-1].remote


def test_consider_uses_csrf_and_cursor():
    def h(method, url, kw):
        if method == "GET":
            return Resp(text='..."csrfToken":"TOK123","board":{"id":"union-square-ventures"}...')
        assert kw["headers"]["x-csrf-token"] == "TOK123"
        body = kw["json"]
        assert body["grouped"] is False and body["board"]["id"] == "andreessen-horowitz"
        if "locations" in body["query"]:
            if "sequence" not in body["meta"]:
                return Resp({"jobs": [{"jobId": "a1", "title": "Chief of Staff", "companyName": "Talos",
                                       "locations": ["New York, NY"], "url": "https://ats/a1",
                                       "salary": {"minValue": 110000, "maxValue": 130000,
                                                  "currency": {"value": "USD"}, "period": {"value": "year"}},
                                       "timeStamp": "2026-10-26T23:41:52Z"}],
                             "meta": {"sequence": "CUR"}, "total": 2})
            return Resp({"jobs": [{"jobId": "a2", "title": "Investment Associate", "companyName": "Z",
                                   "locations": ["NYC"], "url": "https://ats/a2"}], "meta": {}, "total": 2})
        return Resp({"jobs": [{"jobId": "a1", "title": "Chief of Staff", "companyName": "Talos",
                               "locations": ["New York, NY"], "url": "https://ats/a1", "remote": True}],
                     "meta": {}, "total": 1})

    src = {"name": "a16z", "type": "consider", "domain": "jobs.a16z.com", "api_domain": "jobs.usv.com",
           "board_id": "andreessen-horowitz"}
    s = FakeSession(h)
    jobs = fetch_consider(src, s, Throttle(0))
    assert sorted(j.external_id for j in jobs) == ["a1", "a2"]  # deduped across the two queries
    assert s.calls[0][1] == "https://jobs.usv.com/jobs"
    a1 = [j for j in jobs if j.external_id == "a1"][0]
    assert a1.url == "https://ats/a1" and a1.posted == "2026-10-26"


def test_lowercarbon_pages_until_total():
    def h(method, url, kw):
        page = kw["params"]["page"]
        items = [getro_job(page * 1000 + i, "Head of Strategy", "New York, NY, USA") for i in range(100 if page == 1 else 5)]
        for it in items:
            it["company"] = it.pop("organization")
            it["url"] = "https://jobs.ashbyhq.com/x"
        return Resp({"items": items, "meta": {"total": 105}})

    jobs = fetch_lowercarbon({"name": "Lowercarbon", "type": "lowercarbon", "domain": "lowercarbon.com"},
                             FakeSession(h), Throttle(0))
    assert len(jobs) == 105 and jobs[0].company == "Acme" and jobs[0].url.startswith("https://jobs.ashbyhq.com")


def test_traction_rows():
    rows = [{"t": "Strategic Finance Associate", "s": "Fi", "f": "Baukunst ; Lerer Hippeau", "l": "New York, NY",
             "p": "United States", "g": "North America", "d": "2026-10-26", "u": "https://ats/fi", "r": 0, "$": ""}]
    s = FakeSession(lambda m, u, kw: Resp(rows))
    jobs = fetch_traction({"name": "Traction", "type": "traction", "categories": ["ops"], "group": "aggregator"},
                          s, Throttle(0))
    assert jobs[0].funds == ["Baukunst", "Lerer Hippeau"] and jobs[0].locations == ["New York, NY, United States"]


def _job(title, loc, remote=False, **kw):
    from jobwatch.models import Job
    base = dict(source="FirstMark", group="vc", external_id=title + loc, title=title, company="Acme",
                url="https://x", locations=[loc], remote=remote)
    base.update(kw)
    return Job(**base)


def test_filters():
    f = JobFilter(CFG)
    keep = [
        _job("Chief of Staff to the CEO", "New York, NY, USA"),
        _job("Strategic Finance Manager", "New York City"),
        _job("Investment Associate", "Brooklyn, NY"),
        _job("Founder's Associate", "Remote", remote=True),
        _job("Business Operations Lead", "United States; Remote", remote=True),
        _job("Strategy & Operations Manager", "NYC"),
        _job("Chief of Staff, Clinical", "New York, NY"),                 # always_include beats exclude
    ]
    drop = [
        _job("Chief of Staff", "San Francisco, CA, USA"),                 # wrong city
        _job("Senior Software Engineer", "New York, NY, USA"),            # engineering
        _job("Account Executive, Strategic", "New York, NY, USA"),        # sales
        _job("Strategic Finance Intern", "New York, NY, USA"),            # intern
        _job("Head of Strategy", "Remote - Canada", remote=True),         # remote, other country
        _job("Customer Success Manager", "New York, NY, USA"),
        _job("Principal Engineer", "New York, NY, USA"),
        _job("Executive Assistant to the Chief of Staff", "New York, NY"),  # hard_exclude
    ]
    assert [j.title for j in keep if not f(j)] == []
    assert [j.title for j in drop if f(j)] == []


def test_dedupe_prefers_vc_board_and_merges():
    a = _job("Chief of Staff", "New York, NY", source="Traction", group="aggregator", funds=["Thrive Capital"])
    b = _job("Chief of Staff", "New York, NY", source="Thrive Capital", external_id="99")
    out = dedupe([a, b])
    assert len(out) == 1 and out[0].source == "Thrive Capital" and out[0].also_on == ["Traction"]


def test_seen_store_roundtrip(tmp_path):
    st = SeenStore(tmp_path / "seen.json", retention_days=30)
    j = _job("Chief of Staff", "New York, NY")
    assert st.is_new(j)
    st.mark([j], "2026-10-01")
    st.save()
    st2 = SeenStore(tmp_path / "seen.json", retention_days=30)
    assert not st2.is_new(j)
    # Same job showing up on another board is not "new" either
    assert not st2.is_new(_job("Chief of Staff", "New York, NY", source="Traction", external_id="zz"))
    st2.prune("2026-12-15")
    assert st2.is_new(j)


def test_scoring_parse_and_report():
    jobs = [_job("Chief of Staff", "New York, NY"), _job("Venture Associate", "New York, NY"),
            _job("Business Development Rep Lead", "New York, NY")]
    apply_scores(jobs, 'Sure:\n[{"i":1,"score":8,"reason":"Strategic CoS"},{"i":2,"score":9,"reason":"VC"},'
                       '{"i":3,"score":2,"reason":"Sales"}]')
    assert [j.score for j in jobs] == [8, 9, 2]
    b = bucket(jobs, 4)
    assert [j.title for j in b["investing"]] == ["Venture Associate"]
    assert [j.title for j in b["top"]] == ["Chief of Staff"] and len(b["low"]) == 1
    html = build_html(jobs, [{"name": "FirstMark", "fetched": 10, "new": 3}, {"name": "X", "error": "boom"}], CFG,
                      "2026-11-02")
    assert "Strategic CoS" in html and "Could not read" in html and "<script" not in html


def test_first_run_since_then_weekly(tmp_path, monkeypatch):
    """First run reports only jobs posted on/after first_run_since; later runs report only unseen jobs."""
    import argparse
    import jobwatch.__main__ as m

    root = tmp_path
    (root / "data").mkdir()
    cfg = dict(CFG, sources=[{"name": "Fake", "type": "fake", "group": "vc"}],
               scoring={"enabled": False}, report={"output_dir": "reports"}, first_run_since="2026-10-01")
    (root / "config.yaml").write_text(yaml.safe_dump(cfg))
    live = [_job("Chief of Staff", "New York, NY", source="Fake", external_id="old", posted="2026-09-15", company="A"),
            _job("Strategic Finance Lead", "New York, NY", source="Fake", external_id="oct", posted="2026-10-08", company="B")]
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setitem(m.FETCHERS, "fake", lambda src, s, t: list(live))
    reported = {}
    monkeypatch.setattr(m, "build_html", lambda jobs, *a, **k: reported.setdefault("jobs", [j.external_id for j in jobs]) and "")
    args = argparse.Namespace(config="config.yaml", no_email=True, no_score=True, dry_run=False, all=False,
                              only=None, since=None, verbose=False, no_sheet=True)

    m.run(args)
    assert reported.pop("jobs") == ["oct"]          # Sept job skipped...
    live.append(_job("Venture Associate", "New York, NY", source="Fake", external_id="new", posted="2026-11-01",
                     company="C"))
    m.run(args)
    assert reported.pop("jobs") == ["new"]          # ...and never resurfaces; only the new posting shows


def test_posted_since_reports_seen_jobs_in_window(tmp_path, monkeypatch):
    """--all --since: re-show already-reported jobs posted in the window; nothing older."""
    import argparse
    import jobwatch.__main__ as m

    (tmp_path / "data").mkdir()
    cfg = dict(CFG, sources=[{"name": "Fake", "type": "fake", "group": "vc"}],
               scoring={"enabled": False}, report={"output_dir": "reports"}, first_run_since="2026-10-01")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    live = [_job("Chief of Staff", "New York, NY", source="Fake", external_id="a", posted="2026-10-15", company="A"),
            _job("Strategic Finance Lead", "New York, NY", source="Fake", external_id="b", posted="2026-10-24", company="B")]
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setitem(m.FETCHERS, "fake", lambda src, s, t: list(live))
    seen = []
    monkeypatch.setattr(m, "build_html", lambda jobs, *a, **k: seen.append(sorted(j.external_id for j in jobs)) or "")
    base = dict(config="config.yaml", no_email=True, no_score=True, dry_run=False, only=None, verbose=False, no_sheet=True)
    m.run(argparse.Namespace(**base, all=False, since=None))       # first run reports both
    m.run(argparse.Namespace(**base, all=True, since=m._iso_date("2026-10-22")))
    assert seen == [["a", "b"], ["b"]]
