# Roadmap

Growing Werkstudent Radar into a portfolio project across data engineering,
statistics, NLP and product work. Tick items off as they land (one commit per
step), so progress is visible here and not only in the commit history.

Rules for every phase:
- Incremental: no rewrites. `docs/data/*.json` stay backward compatible (only new
  files or new fields; `checker.json` rows never reordered).
- No frontend framework or build step; new `<script>`/`<link>`/imports carry `?v=dev`.
- New Python dependencies go into `requirements.txt`; the daily run stays fast.
- The site gets aggregates only, never job texts.
- Commits only after the owner's OK; the owner pushes.

## Decisions

- **History store (Phase 1).** A daily-updated binary SQLite file would change on
  most pages every day (every active posting's `last_seen` moves) and grow the git
  repository by megabytes a day. So the committed source of truth is plain text:
  `data/jobs.csv` (one row per posting, already kept forever) plus small,
  append-mostly CSVs in `data/history/`. `data/history.sqlite` is rebuilt from
  them in seconds (`python -m radar.history`) and is git-ignored.
- **Fewer sections (after Phase 7).** The "What does your degree lead to?" section
  and the SQL playground (Phase 6) were taken off the site to keep it focused. The
  Parquet export and the `duckdb` dependency went with the playground; the degree
  data stays in `summary.json` (formats only grow) and the CV check still names the
  degree it finds.
- **No SmartRecruiters adapter (Phase 8).** `api.smartrecruiters.com/robots.txt`
  disallows every crawler except LinkedInBot, and the official Posting API now asks
  for a key, so SmartRecruiters boards are detected but not read (AUTO1, Bosch,
  Drees & Sommer stay `enabled: false`). Greenhouse, Lever, Ashby and Personio
  publish keyless job-board endpoints that their robots.txt allows.

## Phase 1 — Data engineering: history

- [x] SQLite history database `data/history.sqlite` (built from committed CSVs, see
      Decisions): `postings` keyed by refnr (first_seen, last_seen, field, city,
      lat/lon, pay, German requirement, skills) plus `scans` (one row per collection
      day and source) and `online` (intervals of consecutive scans a posting was
      seen in). Daily run upserts; postings missing today keep their last_seen.
- [x] Backfill from what exists: scans from `history.csv`/`history.json`, intervals
      from `jobs.csv` first_seen/last_seen.
- [x] New aggregate file for the site (daily postings online / new / gone), no raw texts.
- [x] Daily workflow runs the history step and commits `data/history/`.

## Phase 2 — Quality: tests + data checks

- [x] pytest for pay parsing ("17,50 € pro Stunde" etc.), skill/German detection,
      dedup and the upsert logic (extend the existing 133 tests where they already cover it).
- [x] Data-quality checks in the pipeline: fail the run if the posting count drops or
      jumps >50% vs. the 7-day median, required API fields are missing, or pay values
      fall outside a sane range. A failing run opens/updates the GitHub issue.
- [x] Tests in CI on every push (`tests.yml`, now without the data-only path filter) and a
      status badge in the README (both badges already existed).

## Phase 3 — Statistics

- [x] Bootstrap 95% confidence intervals (2000 resamples, fixed seed) for every median
      pay shown; shown as a subtle range ("€16.50 · 95% CI €15.40–17.60"); medians with
      a very wide CI or n<10 hidden (current n≥10 rule stays).
- [x] Posting lifetime from first_seen/last_seen: Kaplan–Meier curve (still-online
      postings censored), median days online overall and by field; new chart in Trends.
      Age is counted from the publication date with delayed entry (postings are only
      observed from their first scan); Bundesagentur postings only.
- [x] Skill co-occurrence: for each skill, top co-occurring skills with lift.

## Phase 4 — Product: skill gap + alerts

- [x] "Check your skills": skill-gap suggestions ("learn X and Y to qualify for N more
      jobs"), client-side from `checker.json`, greedy by marginal gain.
- [x] RSS feeds generated daily: all new postings and one per field (title, company,
      city, link to the original, no job text); small "Subscribe" control in the UI.

## Phase 5 — NLP experiment

- [x] `experiments/`: skill extraction with multilingual sentence embeddings or a small
      zero-shot model vs. the current rules on the 100 labelled postings (labels are
      AI-made, see `data/eval/LABELS.md`); precision/recall/F1 per skill and overall.
- [x] Recommendation: replace/combine with the rules only if clearly better. The daily
      pipeline is not touched in this phase. Result: rules micro F1 95% vs. embeddings 75%
      (rules or embeddings 91%), so the rules stay; see `experiments/README.md`.
      Experiment dependencies live in `experiments/requirements.txt`, not the project's,
      so the daily run and CI don't install them.

## Phase 6 — Extra frontend

- [x] Optional SQL playground: DuckDB-WASM lazy-loaded from jsDelivr only when opened,
      over a Parquet export of aggregated postings (no texts); 3–4 example queries.
- [x] Lighthouse CI (desktop) in GitHub Actions; Performance, Accessibility,
      Best Practices ≥ 90.
- [x] German/English UI toggle: strings in one dictionary, language in the URL.

## Phase 7 — Packaging

- [x] README: Mermaid architecture diagram (BA API → pipeline → SQLite → build →
      docs/ → Pages), screenshots, live link, key numbers (postings per day, days of
      history, skill-detection precision/recall) and a "Methods" section (bootstrap,
      Kaplan–Meier, lift).

## Phase 8 — Company career pages (Berlin & München)

A "Companies" tab with Werkstudent / working student jobs in Berlin and München read
directly from company career pages through the public job-board APIs of their
applicant tracking systems (ATS). No per-site scrapers; LinkedIn and Indeed are
never fetched. Stop after each step for review.

- [x] Step 1 — company list: `data/companies.yaml` (name, cities, careers URL, ats,
      slug, enabled, plus `ba_names` for matching Bundesagentur employers and a
      detection `note`), seeded with employers from our BA postings in Berlin and
      München and extended by hand. `python -m radar.companies detect` finds the ATS
      (Greenhouse, Lever, Ashby, Personio, SmartRecruiters) from redirects and links;
      anything else stays `unsupported`.
- [x] Step 2 — adapters: one small adapter per ATS on its official public job-board
      endpoint (endpoint and terms checked first), normalised to company, title, city,
      url, posted_at, source_ats. Keep titles with Werkstudent / Working Student /
      Werkstudierende in Berlin or München (remote in Germany flagged separately).
      robots.txt and terms respected, identifying User-Agent, 1–2 s between
      companies, retries with backoff.
- [x] Step 3 — pipeline and quality: daily run writes `docs/data/companies.json`
      (no job texts, links to the originals), deduped against BA postings (company +
      normalised title + city) with an "also on BA" mark; run-log warnings for a 404
      or 0 jobs after having jobs; pytest per adapter on saved sample responses.
- [x] Step 4 — frontend: "Companies" tab in the radar style with a Berlin / München
      switch, list grouped by company with job counts, field keyword filter, "new
      today" badge and links to the originals; works with the phone tab bar and
      filter sheet.

## Phase 9 — MCP server: ask Claude about the data

An MCP server in `mcp/` so Claude (Desktop or Code) can answer questions with the
radar's data. Read-only; the website, the Python pipeline and the data format
stay as they are. Stop after each step for review.

- [x] Step 1 — setup: `mcp/` with a stdio server on the official MCP Python SDK
      (its own `requirements.txt` and git-ignored `.venv/`, like `experiments/`).
      Reads `docs/data/*.json` from the repo by default, or with `--live` the
      published JSON from GitHub Pages with a short in-memory cache. Reuses
      `summary.json`, the positional `checker.json` rows and `postings.json`
      (matched by position and `as_of`).
- [ ] Step 2 — tools: `get_overview`, `search_jobs`, `pay_stats`, `top_skills`,
      `match_skills`, with typed parameters and descriptions; compact JSON, never
      job texts.
- [ ] Step 3 — quality: pytest per tool on a small fixture copy of the data;
      unknown city/field/skill answered with close matches.
- [ ] Step 4 — docs: README "Use with Claude" (Claude Desktop config, `claude mcp
      add`), three example questions, tried in a Claude Code session.
- [ ] Step 5 — a plain-language explanation of MCP, the request flow and each file,
      with likely interview questions.
