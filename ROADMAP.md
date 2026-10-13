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
- **Hosting on Vercel (Phase 10).** The plan compared paid-capable hosts; the owner
  wants no costs at all, so the server runs on Vercel's Hobby plan: free, no payment
  method, and past a limit it pauses instead of billing. Google Cloud Run would be
  free at this traffic too but needs a card; Render's free tier needs none but takes
  about a minute to wake up. The Dockerfile stays for CI and other container hosts.
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
- [x] Step 2 — tools: `get_overview`, `search_jobs`, `pay_stats`, `top_skills`,
      `match_skills`, with typed parameters and descriptions; compact JSON, never
      job texts.
- [x] Step 3 — quality: pytest per tool on a small fixture copy of the data;
      unknown city/field/skill answered with close matches.
- [x] Step 4 — docs: README "Use with Claude" (Claude Desktop config, `claude mcp
      add`), three example questions, tried in a Claude Code session. Registered
      with the README's `claude mcp add` (connected; a new Claude Code session lists
      the five tools); the questions were asked through the server over stdio.
      Testing showed `match_skills` should also return a few fitting postings
      (`examples`).
- [x] Step 5 — a plain-language explanation of MCP, the request flow and each file,
      with likely interview questions.

## Phase 10 — Remote MCP server: connect Radar to Claude by URL

The Phase 9 server, also reachable over the internet so anyone can add it to
Claude by URL. The local stdio mode keeps working; read-only public data, no
login, never secrets in the repository. Stop after each step for review.

- [x] Step 1 — remote transport: Streamable HTTP (the SDK's recommended remote
      transport) next to stdio with the same tools, `--http`. Stateless JSON
      responses; live JSON from GitHub Pages cached for an hour; per-client and
      global rate limits, a 64 KiB request body limit, Host header check
      (`MCP_ALLOWED_HOSTS`), `/health`, and a request log without client addresses.
- [x] Step 2 — hosting: compare three hosts with a free or very cheap tier
      (current terms, cold starts, limits), recommend one; Dockerfile and a GitHub
      Actions workflow that deploys on pushes to main changing `mcp/`; the account
      and GitHub secret steps for the owner.
- [x] Step 3 — use it: test the deployed URL with the MCP Inspector, as a custom
      connector in claude.ai and in Claude Code with the three example questions;
      README "Use Radar in Claude" and a short "Use in Claude" section on the site.
      Live at `https://radar-mcp.vercel.app/mcp`; answered the three questions through
      the SDK client, the Inspector CLI, Claude Code, a claude.ai connector ("No
      sign-in") and that connector inside a Claude Code session. Testing showed the
      SDK's indented text copies cost about a third more tokens, so the tools now
      send compact JSON.
- [x] Step 4 — a plain-language explanation: local vs. remote MCP, hosting, Docker
      and the deploy pipeline, cold starts, rate limits, the request flow, and
      likely interview questions.

## Phase 11 — Application tracker, no section numbers

Visitors keep track of their own applications on the site. Stored only in their
browser (localStorage) with an export/import file; nothing leaves the device. The
sections lose their numbers. Stop after each step for review.

- [x] Step 1 — design without numbers: each section starts with an icon on a
      split-flap tile and its name; the phone tab bar shows the same icons (inline
      SVG sprite, no icon library).
- [x] Step 2 — tracker panel: an "Applications" button with a count in the header
      opens a panel on the right (full screen on phones): applications by status
      (saved, applied, interview, offer, rejected) with the date of each change,
      notes, a follow-up date with overdue ones highlighted, delete, export/import,
      and a form for a new application (moved here from step 3, so the panel works
      on its own).
- [x] Step 3 — adding from the radar: a "Track" button on every job in "Check your
      skills" and on the Companies tab, and a "no longer online" mark when a tracked
      posting leaves the daily data.
- [x] Step 4 — tests for the tracker logic (Node), README and CLAUDE.md, Lighthouse.

## Phase 12 — Telegram job alerts (webhook + n8n)

New postings reach Telegram every morning: the daily pipeline sends one signed
webhook, an n8n workflow filters and formats it, a Telegram bot posts it. The site,
the pipeline and the data format stay as they are; secrets live only in GitHub
Secrets and n8n credentials. Stop after each step for review.

- [x] Step 1 — new postings + webhook: `radar/notify.py` picks the postings first seen
      on the latest collection day (same rules as the RSS feeds, from the history's
      first_seen) plus new jobs found only on company career pages, and POSTs one
      compact JSON payload (title, company, city, field, skills, pay, German, link; no
      job texts) to `N8N_WEBHOOK_URL`, signed with HMAC-SHA256 over timestamp and body
      (`WEBHOOK_SECRET`). Runs after the data commit; without the secrets it does
      nothing, and a failed call is a warning, never a failed run. pytest for the
      selection, payload, signature and sending.
- [x] Step 2 — n8n workflow in `integrations/n8n/`: Webhook → check signature →
      filter (city, fields, skills, no German) → short Telegram message (≤10 jobs,
      then "+N more" with a link to the site) → Telegram; a public channel with all new
      Berlin jobs and the owner's personal chat with its own filter. Setup guide
      (bot, chat IDs, hosting, import, credentials, GitHub Secrets). The Code nodes
      live in `code/*.js` (tested in Node) and are built into the importable JSON;
      self-hosting with Docker + ngrok's free domain is recommended over the 14-day
      Cloud trial. Tried in a local n8n 2.43.4, which showed that its Code node has
      no `URLSearchParams` and that a repeated day must be marked only after the
      messages are built.
- [ ] Step 3 — test: a manual `workflow_dispatch` that sends a test payload, and a
      "Get alerts on Telegram" link on the site to the public channel.
- [ ] Step 4 — a plain-language explanation: webhooks, why n8n, the data flow, the
      signature, likely interview questions.
