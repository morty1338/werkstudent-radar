# Werkstudent Radar

Daily statistics of Werkstudent postings in Germany: a Python pipeline
(`radar/`, run by `.github/workflows/daily.yml`) writes JSON into `docs/data/`,
and a static site in `docs/` (GitHub Pages, deployed by `pages.yml`) shows it.

## Roadmap

Planned phases (history DB, data checks, statistics, skill gap and RSS, NLP
experiment, extra frontend, packaging) are checklists in `ROADMAP.md`. Read it
first and tick items off as they land; its "Decisions" section records choices
that differ from the original plan.

## Experiments

`experiments/` holds side studies outside the daily pipeline, with their own
`requirements.txt` and git-ignored `.venv/` and `.cache/`. They read the local
job texts in `data/eval/sample.json` and must only write scores and reference
numbers, never job text.

## MCP server

`mcp/` holds a read-only MCP server (official MCP Python SDK v2, `MCPServer`)
that answers questions with the site's JSON (`docs/data/`, or the published files
with `--live`). `radar_tools.py` holds the logic without the SDK; `radar_server.py`
only wraps it. Own `requirements.txt` and git-ignored `.venv/`, so the daily run
doesn't install the SDK; `requirements-dev.txt` includes it, so `tests.yml` runs
`tests/test_mcp.py` (tools on a hand-made fixture, the server through the SDK's
in-memory client, agreement with `gap.js` and `summary.json`). The folder has no
`__init__.py` and its modules are named `radar_*.py`: a module or package called
`mcp` would shadow the SDK. On stdio nothing may be printed to stdout (logs go to
stderr).

`--http` serves the same tools over Streamable HTTP at `/mcp` (`radar_http.py`:
stateless JSON responses, rate limits, 64 KiB body limit, `/health`, Host check
from `MCP_ALLOWED_HOSTS`, log without client addresses; uvicorn's access log stays
off). In HTTP mode the data is the live JSON, cached for an hour.
`tests/test_mcp_http.py` runs a real uvicorn on a free port, and the staged Vercel
deployment in its own process.

Hosting: Vercel Hobby (free, no payment method; `mcp/DEPLOY.md` compares hosts).
`mcp/vercel/deploy.sh` stages the deployment with `mcp/vercel/stage.sh` (entrypoint
`mcp/vercel/index.py` as `api/index.py` with `trust_proxy=True` and any Host, every
path rewritten to it; `mcp/radar_*.py`, `radar/stats.py`) outside the checkout and
deploys it; Vercel only finds Python apps without FastAPI/Flask/Django in `api/`.
`mcp-deploy.yml` tests, builds and smoke-tests `mcp/Dockerfile` (built from the repo
root; `mcp/Dockerfile.dockerignore`) on every change to `mcp/`, and deploys only
when the secret `VERCEL_TOKEN` exists. Never connect the repository to Vercel's Git
integration: it would deploy everything on every bot commit. Failing workflow steps
repeat their output as public annotations (`.github/scripts/run_logged.sh`): read
them via the check-runs annotations API, since the logs need the owner's login.

## Frontend notes

**Files** (`docs/`, served as-is, no build step):
- `index.html` — all sections and element ids; `favicon.svg` (the logo, amber radar). Sections have no
  numbers: each starts with `.eyebrow` (an icon on a split-flap tile + the nav label, `data-i18n`), and the
  phone tab bar shows the same icons. The icons are `<symbol>`s in the inline sprite at the top of `<body>`
  (24×24, stroked with `currentColor`), used as `<svg class="ico"><use href="#i-…">`
- `manifest.json`, `icons/` — PWA manifest and PNG icons (180 apple-touch, 192, 512, also
  used as maskable: logo at 72% on a full `#0B0B0C` square). Made once from
  `favicon.svg` (headless Chrome screenshot at 512 px, then `sips -z`); redo them if the logo changes
- `assets/style.css` — the only stylesheet
- `assets/app.js` — state, filter bar, the sections where, pay, skills and trends, URL sync
- `assets/charts.js` — chart helpers (`barList`, `histogram`, `dotRange`, `bubbleMap`, `lineChart` with optional CI band, `survivalChart`), tooltip, `rollDigits` (KPI tile animation, only for values that changed), `fmt`, `esc`
- `assets/match.js` — the "Check your skills" section: skill chips, match summary, collapsible job list
- `assets/companies.js` — the "Company career pages" section: city switch (Berlin/München, `?co=muenchen`), field chips, keyword search, jobs grouped by company from `data/companies.json`; hidden with its nav link when the file is missing. Not tied to the filter bar
- `#claude` card in `index.html` ("Ask Claude about these jobs"): the hosted MCP server URL with a copy button (`initCopyUrl` in `app.js`) and the connector steps; the URL also appears in `claude.steps` in `i18n.js` and in the README
- `assets/tracker.js`, `assets/tracker-store.js` — the visitor's application tracker: header button
  `#trk-open` (count, warning colour when a follow-up is due) opens the panel `#trk` (a dialog on the right,
  full screen on phones; Esc and the backdrop close it, focus stays inside). The data lives only in
  `localStorage` under `werkstudent-radar.applications` (`{version, items}`); the store has no DOM and
  works in memory when storage is blocked. Export/import is a JSON file (import merges by id, newer change
  wins). The panel sits outside `.site-header` (z-index 55/60, above the tab bar and the filter sheet)
- `assets/i18n.js` — UI language (`?lang=de`, default English): `STRINGS` holds every UI text with `en` and `de` side by side; `t(key, vars)`; `fieldLabel`/`groupLabel`/`skillLabel` translate data labels; `applyStatic()` fills `data-i18n` (text), `data-i18n-html` and `data-i18n-attr="attr:key"` in `index.html`. New UI text goes into `STRINGS`, never inline (a test checks every used key exists in both languages). `fmt` in `charts.js` formats numbers/dates for the language
- `assets/gap.js` — skill-gap plan (`gapPlan`, greedy by marginal gain; no imports, tested in Node)
- `assets/stats.js` — bootstrap 95% CI of a median (`medianCI`, same method as `radar/stats.py`, memoised)
- `assets/cv.js` — CV upload (PDF via pdf.js 4.10.38 from cdnjs on demand, DOCX via `DecompressionStream`, TXT), skill detection with `data/patterns.json`
- `data/` — `summary.json`, `checker.json` (per-posting arrays), `postings.json` (titles, lazy-loaded, ~740 KB), `history.json`, `patterns.json`, `og.png`, generated by `radar/build.py`; `lifetimes.json` (Kaplan–Meier), `cooccurrence.json` (skill lift), also from `build.py`; `timeline.json` (online/new/back/gone per day), generated by `radar/history.py`. `companies.json` (Werkstudent jobs from company career pages in Berlin/München: `jobs` with first_seen/new/also_on, `companies` with board status), generated by `radar/careers.py` from `data/companies.yaml` through the ATS adapters in `radar/ats/`. `lifetimes`/`cooccurrence` are optional for the page (loaded with `.catch(() => null)`)

**CSS:** one file, no framework. Design tokens as CSS variables on `:root`
(`--bg`, `--surface`, `--surface-2`, `--surface-3`, `--ink`, `--ink-2`, `--ink-3`,
`--line`, `--accent`, `--accent-ink`, `--on-accent`, `--accent-soft`, `--muted-bar`,
`--mono`, …). Dark only: there is no light theme and no `data-theme`. No hex
colors outside `:root`. Breakpoints: 960, 860, 560 px.

**Charts:** hand-written, no chart library. Bars, histogram and dot-range rows
are HTML divs; the place map and line charts are inline SVG. The map is an
equirectangular projection squashed by cos(51.2°) with fixed Germany bounds;
the country outline (`DE_OUTLINE`, Natural Earth 1:50m, inline in `charts.js`)
uses the same projection. Bubble radius ∝ √jobs (max 19.5 units); places without
coordinates are skipped. A radar sweep (SMIL `animateTransform`, clipped to the
outline) is hidden under `prefers-reduced-motion`. One shared `#tooltip` shows
the HTML in any element's `data-tip` (escape with `esc()`): hover with a mouse,
tap on touch (stays until a swipe or a tap elsewhere). Inside `[data-near]` (the
map) a tap within 22 px of a small bubble picks the nearest one.

**State and navigation:** `state` in `app.js` (`field`, `city`, `noGerman`,
`payBy`, `skillGroup`, `skill`); selected skills live in `match.js`.
`syncUrl()` mirrors them to `?field=&city=&german=no&skills=` (plus `co=` from `companies.js`) via
`history.replaceState`; they are read back on load. Chart clicks use delegated
`data-key` handlers (`onPick`) and call `render()`, which redraws every
filtered section; trends are not filtered. Section anchors:
`#where #pay #skills #trends #check #companies`, under a sticky header with the
filter bar; an IntersectionObserver sets `aria-current` on the nav link in view.
Active filters also show as removable chips in `#f-chips` (phones only).

**Fonts and colors:** text uses the system font stack (`ui-sans-serif,
system-ui, -apple-system, …`). Numbers (KPI values, axis labels, section numbers,
bar values, pay) use `var(--mono)`: JetBrains Mono, self-hosted in
`assets/fonts/` (SIL OFL, `OFL.txt`), with `tabular-nums`. Never load fonts from
Google or another CDN (visitors' IPs would go to a third party). Palette: background
`#0B0B0C`, surfaces `#141416` / `#1D1D20` / `#2A2A2E`, accent amber `#FFB000`
(text `#FFC247`), text on amber is dark (`--on-accent`). Highlighted marks are
amber; non-highlighted, dimmed and low-confidence marks use `--muted-bar` `#6B6352`.

**Fragile:**
- `feeds/` — RSS feeds written by `radar/feeds.py` (via `build.py`); the feed `<link rel="alternate">` and links
  deliberately have no `?v=dev` (subscribed URLs must stay fixed)
- Every module import and the `<link>`/`<script>`/logo `<img>` in `index.html` carry `?v=dev`;
  `pages.yml` replaces it with the commit hash (Pages caches files for 10 min).
  New modules must use the same suffix, or a module loads twice.
- `checker.json` rows are positional (`J` indices in `app.js`); `postings.json`
  is matched to it by position and `as_of`.
- JS looks up many element ids from `index.html`; renaming an id fails silently.
- Anchor offsets (`scroll-padding-top`, `.skill-detail` top) use `--header-h`, built from
  `--header-row-h` and `--filter-row-h`; change those, not the offsets.
- Phones (≤560 px): `.nav` becomes a fixed bottom tab bar and `#f-sheet` (the filter
  controls, `display: contents` on wide screens) a bottom sheet behind `#f-open`.
  Both are inside `.site-header`, so it must not get `backdrop-filter`/`transform` there.
  Form fields stay ≥16px on phones, or iOS zooms in and the page scrolls sideways.
- Headless Chrome can't render narrower than ~500 px; check mobile layout with a
  real 375 px viewport.
- Lighthouse CI (`lighthouse.yml`, `lighthouserc.json`) fails below 90 in Performance,
  Accessibility or Best Practices. Keep JS-filled text from shifting the layout
  (`.insight[id]` reserves two lines; filter selects have fixed widths on desktop).
  Run locally: `npx --yes @lhci/cli@0.14.0 autorun` (needs Chrome).
- Local preview: `python3 -m http.server --directory docs`.

**Planned redesign:** "Radar / departure board" style, done incrementally —
no rewrite, no framework, no build step. The Python pipeline and the data
format (`docs/data/*.json`) must not change.
