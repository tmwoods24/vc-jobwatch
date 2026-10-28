# vc-jobwatch

A weekly email of **new** NYC roles from 17 VC portfolio job boards plus the tractionjobs.com aggregator.
The roles are filtered to the titles you care about and ranked by Claude for fit against your profile.

It runs free on GitHub Actions every Monday morning. You can also run it locally.

```
boards ──► fetch (official JSON endpoints each site's own page uses)
       ──► filter: NYC / US-remote + target titles (config.yaml)
       ──► dedupe across boards ──► drop anything already reported (data/seen.json)
       ──► Claude scores fit 1-10 vs profile.md
       ──► HTML email + reports/<date>.html + rows in your Google Sheet tracker
                 ▲                                         │
                 └──── your 👍/👎 ratings and notes ◄──────┘  (the scorer learns from them)
```

See `docs/sample-report.html` for what the email looks like.

## How each board is read

| Platform | Boards | Method |
|---|---|---|
| **Getro** | FirstMark, Lerer Hippeau, Greycroft, Primary, Insight, Thrive, General Catalyst, Khosla, EIP, Lux, BEV, The Engine, Prelude | Reads the board's `network_id` from its page, then calls `POST api.getro.com/api/v2/collections/{id}/search/jobs` with a server-side NYC + Remote location filter |
| **Consider** | USV, Promus, a16z | Reads a CSRF token from the board page, then pages through `POST /api-boards/search-jobs` with `grouped:false`. The website shows only 3 jobs per company; the API returns all of them. |
| **WordPress** | Lowercarbon | `GET lowercarbon.com/wp-json/lowercarboncapital/v1/jobs` |
| **Traction** | tractionjobs.com | Static JSON files (`/data/ops.json`, `/data/leadership.json`), about 15k rows, each tagged with the company's investors |

All of these were checked against the live sites on Oct 27, 2026. Notes:
- **a16z**: jobs.a16z.com is a custom site, but its data lives in Consider under board id
  `andreessen-horowitz`. The tool reads that board through USV's Consider front-end (`api_domain` in
  `config.yaml`). If Consider changes this, a16z will show as "Could not read" at the bottom of the email.
- **The Engine**: configured as the Getro board at `jobs.engine.xyz`. Its companies are also covered by Traction.

If a board breaks, the email names it at the bottom and the other boards still run.

## Setup (about 15 minutes)

1. **Create a private GitHub repo** and push this folder to it. Keep it private: the reports
   and `profile.md` describe your job search.
2. **Add repository secrets** under Settings → Secrets and variables → Actions → New repository secret:

   | Secret | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | from console.anthropic.com. Scoring roughly 50-100 jobs a week costs cents. |
   | `SMTP_USER` | the Gmail address that sends the email |
   | `SMTP_PASSWORD` | a Gmail **app password** (Google Account → Security → 2-Step Verification → App passwords), not your normal password |
   | `EMAIL_TO` | where the report goes (optional; defaults to `SMTP_USER`) |

3. **Edit `profile.md`**. The scorer reads it word for word, so be specific about what you do and don't want.
4. **Run it once by hand**: Actions tab → "Weekly job watch" → Run workflow. The first report covers
   jobs posted since `first_run_since` (Oct 1, 2026 in `config.yaml`). Older matches are remembered silently,
   so they never appear later. From then on, each Monday email has only the jobs that are new since the previous run.

The schedule is Mondays at 11:43 UTC (6:43am Central). To change it, edit the `cron` line in
`.github/workflows/weekly.yml`.

## Tracker (Google Sheet)

Every reported job is also added to a Google Sheet with three tabs:

| Tab | Who fills it | What it's for |
|---|---|---|
| **Jobs** | the weekly run adds rows, newest on top, best score first | Pick **My fit** (👍 Good fit / 🤔 Maybe / 👎 Not a fit), tick **Applied**, and add a **My note** (e.g. "too junior", "love the sector") |
| **Applications** | ticked jobs are copied here every morning; you add your own rows for jobs found elsewhere | Keep **Status** current (Applied → Recruiter screen → Interviewing → Final round → Offer / Rejected / Withdrawn / No response), plus next step, date, contact and notes |
| **Dashboard** | formulas only | Applications by status, response rate, last 7/30 days, upcoming next steps, rating counts |

**How it learns:** before scoring each week, the run reads every rating, your notes, and the applications
you logged yourself, then passes them to Claude as examples of your own judgment. Your ratings take priority over
`profile.md` when the two disagree. A "Not a fit" with a note like "too junior" teaches more than a rating alone.

### Tracker setup (about 10 minutes)

1. **Create a blank Google Sheet** (e.g. "Job search tracker"). Copy its ID from the URL:
   `docs.google.com/spreadsheets/d/`**`<this part>`**`/edit`.
2. **Create a Google service account** (a robot login the tool uses):
   - console.cloud.google.com → create a project (any name) → **APIs & Services → Library** → enable
     **Google Sheets API**.
   - **APIs & Services → Credentials → Create credentials → Service account**. Give it a name and click
     **Done**; it needs no roles.
   - Click the new service account → **Keys → Add key → Create new key → JSON**. A `.json` file downloads.
3. **Share the sheet with the robot**: open the JSON file, copy the `client_email` value
   (`…@….iam.gserviceaccount.com`), and share the Google Sheet with that address as **Editor**.
4. **Add two more repository secrets** on GitHub:

   | Secret | Value |
   |---|---|
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of the downloaded JSON file |
   | `SHEET_ID` | the ID from step 1 |

5. **Actions → "Sync tracker" → Run workflow**. This builds the tabs, dropdowns and dashboard, and loads the
   jobs from earlier reports so you can rate them right away.

Afterwards, the sync runs every morning at about 8:15am Central. Click **Run workflow** to sync right away.
Delete the JSON key file from your Downloads folder once it's saved as a secret.

## Run locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...             # optional; without it, jobs are listed unscored
python -m jobwatch run --no-email --dry-run      # writes reports/<today>.html, doesn't touch seen.json
python -m jobwatch run --only FirstMark --no-email --dry-run -v   # test one board
python -m pytest -q                       # offline tests
```

Flags: `--since YYYY-MM-DD` (report only jobs posted on/after a date), `--no-email`, `--no-score`, `--dry-run` (don't update `seen.json`), `--all` (ignore history and report
every current match), `--only NAME` (repeatable).

## Tuning

Everything lives in `config.yaml`:
- **`titles.include` / `exclude`**: regex lists. Start broad; Claude's score does the fine ranking.
  `always_include` (e.g. Chief of Staff) beats `exclude`; `hard_exclude` (intern, EA) beats everything.
- **`locations`**: NYC patterns, plus `allow_remote` with a list of non-US places to reject.
- **Adding a board**: find the platform (the "Powered by" footer usually says Getro or Consider), then add one line:
  - Getro: `{name: X, type: getro, domain: jobs.x.com, group: vc}`
  - Consider: `{name: X, type: consider, domain: jobs.x.com, board_id: <id>, group: vc}`. To find the
    id, view the page source and search for `"board":{"id":"`.
- **Traction categories**: add `sales` or `tech` if you widen the search.

## Etiquette

The tool makes about 150 requests once a week, with a 1-second gap between calls to the same board, and
identifies itself in the User-Agent. Every endpoint is public and is the same one the board's own page calls.
Keep it at personal-use volume.
