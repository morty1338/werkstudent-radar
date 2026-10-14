// "All jobs" page (kurs.html, internally Kurs-Radar): academy → job title → KPI tiles, the most
// asked-for skills, the postings with links and "+ Track" (the same application tracker as the
// Werkstudent page), and a plain "Opportunities" list. Data: data/kurs.json and, loaded after the
// first render, data/kurs_postings.json (radar/kurs_stats.py). State: ?a=<academy>&t=<job title>.
import { barList, esc, fmt, initTooltip, rollDigits } from "./charts.js?v=dev";
import { applyStatic, initLangSwitch, lang, skillLabel, t } from "./i18n.js?v=dev";
import { MIN_BASE, opportunities, shareOf, wowChange } from "./kurs-rank.js?v=dev";
import { initTracker } from "./tracker.js?v=dev";

const SKILL_ROWS = 10;      // bars shown; the rest is in kurs.json and the MCP tool
const PAGE = 20;            // postings per "show more"

async function load(name) {
  const res = await fetch(`data/${name}.json`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${name}.json: HTTP ${res.status}`);
  return res.json();
}

async function main() {
  applyStatic();
  document.title = t("kurs.doc_title");
  initLangSwitch();
  initTooltip();
  if (lang === "de") document.querySelectorAll(".k-home-link").forEach((a) => (a.href = "index.html?lang=de"));
  // The tracker works without the data, so it starts first; it shares its list with the Werkstudent page.
  const tracker = initTracker();
  let D;
  try {
    D = await load("kurs");
  } catch (e) {
    document.getElementById("k-tiles").innerHTML = `<p class="stale">${esc(t("load.error", { msg: e.message }))}</p>`;
    return;
  }
  const label = (id) => skillLabel(id, D.skills[id]?.label ?? id);
  const titleName = Object.fromEntries(D.academies.flatMap((a) => a.titles.map((x) => [x.id, x.name])));
  const params = new URLSearchParams(location.search);
  const state = { academy: params.get("a"), title: params.get("t"), onlyQe: params.get("qe") === "1", shown: PAGE };
  if (!D.academies.some((a) => a.id === state.academy)) state.academy = D.academies[0].id;

  const fAcademy = document.getElementById("k-academy");
  const fTitle = document.getElementById("k-title");
  const fQe = document.getElementById("k-only-qe");
  const jobsEl = document.getElementById("k-jobs");
  D.academies.forEach((a) => fAcademy.add(new Option(a.name, a.id)));

  const current = () => {
    const a = D.academies.find((x) => x.id === state.academy);
    const ti = a.titles.find((x) => x.id === state.title) ?? null;
    if (!ti) state.title = null;
    return { a, ti, view: ti ?? a };
  };

  function setAcademy(id) {
    if (id !== state.academy) state.title = null;
    state.academy = id;
    state.shown = PAGE;
    render();
  }
  function setTitle(id) {
    state.title = id || null;
    state.shown = PAGE;
    render();
  }
  fAcademy.addEventListener("change", () => setAcademy(fAcademy.value));
  fTitle.addEventListener("change", () => setTitle(fTitle.value));
  fQe.checked = state.onlyQe;
  fQe.addEventListener("change", () => {
    state.onlyQe = fQe.checked;
    state.shown = PAGE;
    renderJobs();
    syncUrl();
  });
  document.getElementById("k-academies").addEventListener("click", (e) => {
    const b = e.target.closest("[data-academy]");
    if (b) setAcademy(b.dataset.academy);
  });
  document.getElementById("k-titles").addEventListener("click", (e) => {
    const b = e.target.closest("[data-title]");
    if (b) setTitle(b.dataset.title);
  });
  document.getElementById("k-chancen").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pick]");
    if (!b) return;
    const [a, ti] = b.dataset.pick.split("/");
    Object.assign(state, { academy: a, title: ti, shown: PAGE });
    render();
    document.getElementById("jobs").scrollIntoView({ behavior: "smooth" });
  });
  document.getElementById("k-more").addEventListener("click", () => {
    state.shown += PAGE;
    renderJobs();
  });

  // Phones: academy and job title in a bottom sheet, the choice as one pill.
  const sheet = document.getElementById("f-sheet");
  const fOpen = document.getElementById("f-open");
  const backdrop = document.getElementById("f-backdrop");
  function setSheet(open) {
    sheet.classList.toggle("open", open);
    backdrop.hidden = !open;
    fOpen.setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("sheet-open", open);
    (open ? fAcademy : fOpen).focus({ preventScroll: true });
  }
  fOpen.addEventListener("click", () => setSheet(true));
  document.getElementById("f-chips").addEventListener("click", () => setSheet(true));
  for (const id of ["f-backdrop", "f-close", "f-done"]) document.getElementById(id).addEventListener("click", () => setSheet(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && sheet.classList.contains("open") && setSheet(false));

  const count = (x) => `${x.postings_exact ? "" : "≥ "}${fmt.int(x.postings)}`;
  const pctOr = (m) => (shareOf(m) == null ? "—" : fmt.pct(m.share, 0));

  function render() {
    const { a, ti, view } = current();
    renderFilters(a, ti);
    renderHeading(a, ti, view);
    renderTiles(view);
    renderSkills(view);
    renderTerms(view);
    renderJobs();
    syncUrl();
  }

  function renderFilters(a, ti) {
    fAcademy.value = a.id;
    fTitle.innerHTML = "";
    fTitle.add(new Option(t("kurs.all_titles"), ""));
    a.titles.forEach((x) => fTitle.add(new Option(x.name, x.id)));
    fTitle.value = ti?.id ?? "";
    fTitle.classList.toggle("active", Boolean(ti));
    document.getElementById("f-chips").innerHTML =
      `<button type="button" class="filter-pill">${esc(a.name)}${ti ? ` · ${esc(ti.name)}` : ""}</button>`;
    document.getElementById("f-count").textContent = t("kurs.as_of_short", { date: fmt.shortDate(D.as_of) });
    document.getElementById("k-academies").innerHTML = D.academies
      .map((x) => `<button type="button" class="chip" data-academy="${esc(x.id)}" aria-pressed="${x.id === a.id}">${esc(x.name)}</button>`)
      .join("");
    document.getElementById("k-titles").innerHTML =
      `<button type="button" class="chip chip-sub" data-title="" aria-pressed="${!ti}">${esc(t("kurs.all_titles"))}</button>` +
      a.titles
        .map((x) => `<button type="button" class="chip chip-sub" data-title="${esc(x.id)}" aria-pressed="${x.id === ti?.id}">${esc(x.name)} <span class="chip-n">${esc(count(x))}</span></button>`)
        .join("");
  }

  function renderHeading(a, ti, view) {
    document.getElementById("k-heading").textContent = ti ? ti.name : t("kurs.academy_heading", { name: a.name });
    const top = view.skills[0];
    const parts = [];
    if (view.sample < MIN_BASE) {
      parts.push(t("kurs.insight_few", { n: fmt.int(view.sample) }));
    } else {
      if (top) parts.push(t("kurs.insight_top", { skill: esc(label(top.id)), pct: fmt.pct(top.share, 0) }));
      if (shareOf(view.quereinsteiger) != null) parts.push(t("kurs.insight_qe", { pct: fmt.pct(view.quereinsteiger.share, 0) }));
      if (shareOf(view.ai) != null) parts.push(t("kurs.insight_ai", { pct: fmt.pct(view.ai.share, 0) }));
    }
    document.getElementById("k-insight").innerHTML = parts.join(" ");
  }

  function renderTiles(view) {
    const few = (m) => t("kurs.too_few", { n: fmt.int(m?.base ?? 0) });
    // An academy's shares weigh its job titles the same: list them in the tooltip.
    const perTitle = (m) => (m?.by_title ? `<br>${esc(t("kurs.equal_weight"))}<br>` + m.by_title
      .map((b) => `${esc(b.name)}: ${b.share == null || b.base < MIN_BASE ? "—" : fmt.pct(b.share, 0)}`).join("<br>") : "");
    const wow = wowChange(view.wow);
    const tiles = [
      {
        href: "#stellen",
        label: t("kurs.tile_postings"),
        value: count(view),
        sub: wow != null
          ? t("kurs.tile_wow", { pct: fmt.signedPct(wow), date: fmt.shortDate(view.wow.before_date) })
          : t("kurs.tile_new7", { pct: fmt.pct(view.new_7d.share ?? 0, 0) }),
        tip: esc(view.postings_exact ? t("kurs.tip_exact") : t("kurs.tip_estimate", { sample: fmt.int(view.sample) })),
      },
      {
        href: "#stellen",
        label: t("kurs.tile_qe"),
        value: pctOr(view.quereinsteiger),
        sub: shareOf(view.quereinsteiger) == null ? few(view.quereinsteiger)
          : t("kurs.tile_of", { n: fmt.int(view.quereinsteiger.n), base: fmt.int(view.quereinsteiger.base) }),
        tip: esc(t("kurs.tip_qe", { text: fmt.int(view.quereinsteiger.text), ba: fmt.int(view.quereinsteiger.ba_flag) })) + perTitle(view.quereinsteiger),
      },
      {
        href: "#k-skills-card",
        label: t("kurs.tile_ai"),
        value: pctOr(view.ai),
        sub: shareOf(view.ai) == null ? few(view.ai) : t("kurs.tile_of", { n: fmt.int(view.ai.n), base: fmt.int(view.ai.base) }),
        tip: esc(t("kurs.tip_ai")) + perTitle(view.ai),
      },
      {
        href: "#k-skills-card",
        label: t("kurs.tile_basics"),
        value: pctOr(view.basics),
        sub: shareOf(view.basics) == null ? few(view.basics) : t("kurs.tile_basics_sub"),
        tip: esc(t("kurs.tip_basics")) + perTitle(view.basics),
      },
    ];
    const box = document.getElementById("k-tiles");
    const before = [...box.querySelectorAll(".value")].map((v) => v.dataset.value);
    box.innerHTML = tiles
      .map((x) => `<a class="tile" href="${x.href}" data-tip="${esc(x.tip)}">
        <span class="label">${esc(x.label)}</span>
        <span class="value" data-value="${esc(x.value)}">${esc(x.value)}</span>
        <span class="sub">${esc(x.sub)}</span></a>`)
      .join("");
    box.querySelectorAll(".value").forEach((v, k) => {
      if (v.dataset.value !== before[k]) rollDigits(v, before[k]);
    });
  }

  // One plain bar per skill: the share of postings that ask for it. Must/nice only in the tooltip.
  function renderSkills(view) {
    const el = document.getElementById("k-skills");
    if (!view.skills.length || view.with_skills < MIN_BASE) {
      el.innerHTML = `<p class="empty">${esc(t("kurs.skills_few", { n: fmt.int(view.with_skills) }))}</p>`;
    } else {
      barList(el, view.skills.slice(0, SKILL_ROWS).map((s) => ({
        label: label(s.id),
        value: s.share,
        text: fmt.pct(s.share, s.share < 0.1 ? 1 : 0),
        tip: `<strong>${esc(label(s.id))}</strong><br>${esc(t("kurs.bar_tip", { n: fmt.int(s.n), base: fmt.int(view.with_skills) }))}` +
          (s.must + s.nice ? `<br>${esc(t("kurs.bar_tip_imp", { must: fmt.int(s.must), nice: fmt.int(s.nice) }))}` : ""),
      })), { dim: false });
    }
    const basics = view.basics_skills;
    document.getElementById("k-basics").innerHTML = basics.length && view.with_skills >= MIN_BASE
      ? `<span class="basics-label" data-tip="${esc(t("kurs.basics_tip"))}">${esc(t("skills.basics"))}</span>` +
        basics.map((s) => `<span class="chip chip-static">${esc(label(s.id))} <span class="chip-n">${fmt.pct(s.share, s.share < 0.1 ? 1 : 0)}</span></span>`).join("")
      : "";
    document.getElementById("k-skills-note").textContent = view.with_skills >= MIN_BASE
      ? t("kurs.skills_note", { n: fmt.int(view.with_skills) })
      : "";
  }

  function renderTerms(view) {
    const terms = view.other_terms ?? [];
    document.getElementById("k-terms").innerHTML = terms.length
      ? terms.map((x) => `<span class="chip chip-static">${esc(x.term)} <span class="chip-n">${fmt.int(x.n)}</span></span>`).join("")
      : `<p class="empty">${esc(t("kurs.terms_none"))}</p>`;
  }

  // --- Postings with links and "+ Track" -------------------------------------------------------
  let P = null;            // kurs_postings.json, loaded after the first render
  let pLoading = null;
  let shownRows = [];
  const col = {};
  function ensurePostings() {
    pLoading ??= load("kurs_postings").then((p) => {
      P = p;
      p.fields.forEach((f, i) => (col[f] = i));
      renderJobs();
      return p;
    }).catch(() => {
      jobsEl.innerHTML = `<p class="empty">${esc(t("kurs.jobs_error"))}</p>`;
      return null;
    });
    return pLoading;
  }
  const posting = (row) => ({
    ref: `k:${row[col.refnr]}`,
    title: row[col.title],
    company: row[col.company],
    city: row[col.city],
    url: P.url.replace("{refnr}", encodeURIComponent(row[col.refnr])),
  });

  function renderJobs() {
    const { a, ti, view } = current();
    document.getElementById("k-jobs-title").textContent = t("kurs.jobs_title", { name: ti ? ti.name : a.name });
    document.getElementById("k-jobs-note").textContent = view.postings_exact ? ""
      : t("kurs.jobs_sample", { n: fmt.int(view.sample), total: count(view) });
    const more = document.getElementById("k-more");
    if (!P) {
      more.hidden = true;
      ensurePostings();
      return;
    }
    const titles = new Set(ti ? [ti.id] : a.titles.map((x) => x.id));
    shownRows = P.rows.filter((r) => titles.has(r[col.job_title]) && (!state.onlyQe || r[col.qe]));
    document.getElementById("k-jobs-count").textContent = t("kurs.jobs_count", { n: fmt.int(shownRows.length) });
    jobsEl.innerHTML = shownRows.length
      ? shownRows.slice(0, state.shown).map((r, i) => jobRow(r, i, !ti)).join("")
      : `<p class="empty">${esc(t("kurs.jobs_none"))}</p>`;
    more.hidden = shownRows.length <= state.shown;
    more.textContent = t("check.more", { n: fmt.int(shownRows.length - state.shown) });
  }

  function jobRow(r, i, showTitle) {
    const job = posting(r);
    const tracked = tracker.isTracked(job.ref);
    const badges = (r[col.qe] ? `<span class="badge en">${esc(t("kurs.badge_qe"))}</span>` : "") +
      (r[col.ai] ? `<span class="badge">${esc(t("kurs.badge_ai"))}</span>` : "");
    const published = r[col.published];
    return `<div class="job">
      <a href="${esc(job.url)}" target="_blank" rel="noopener">${esc(job.title)}</a>
      <div class="meta">${esc(job.company)} · ${esc(job.city || "—")}${published ? ` · ${esc(fmt.shortDate(published))}` : ""}${showTitle ? ` · ${esc(titleName[r[col.job_title]] ?? "")}` : ""}</div>
      <div class="side">${badges}<button type="button" class="track-btn" data-track="${i}" aria-pressed="${tracked}"
        title="${esc(t(tracked ? "trk.tracked_title" : "trk.track_title"))}">${esc(t(tracked ? "trk.tracked" : "trk.track"))}</button></div>
    </div>`;
  }

  // "+ Track" stores the posting in the visitor's applications; once tracked it opens the list.
  jobsEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-track]");
    if (!b || !P) return;
    const job = posting(shownRows[Number(b.dataset.track)]);
    if (tracker.isTracked(job.ref)) tracker.open(job.ref);
    else tracker.track(job);
  });
  tracker.onChange(() => P && renderJobs());
  // Tracked postings of this page ("k:") that are no longer online; the Werkstudent page checks its own.
  tracker.setOnlineCheck(async (refs) => {
    const gone = new Set();
    if (!refs.some((r) => r.startsWith("k:"))) return gone;
    const p = await ensurePostings();
    if (!p) return gone;
    const online = new Set(p.rows.map((r) => r[col.refnr]));
    for (const r of refs) if (r.startsWith("k:") && !online.has(r.slice(2))) gone.add(r);
    return gone;
  });

  // --- Opportunities: a plain ranked list ------------------------------------------------------
  function renderChancen() {
    const { rows, skipped } = opportunities(D);
    document.getElementById("k-chancen").innerHTML = rows
      .map((x, i) => `<li class="${i < 3 ? "top" : ""}"><button type="button" class="k-chance" data-pick="${esc(x.academy.id)}/${esc(x.id)}">
        <span class="k-rank">${i + 1}</span>
        <span class="k-chance-body"><strong>${esc(x.name)}</strong> <small>${esc(x.academy.name)}</small>
        <span class="k-chance-text">${esc(t("kurs.chance_new7", { n: count(x), m: fmt.pct(x.momentum, 0), qe: fmt.pct(x.quereinsteiger.share, 0) }))}</span>
        <span class="k-chance-parts">${esc(t("kurs.chance_parts", { v: x.parts.volume, m: x.parts.momentum, o: x.parts.openness }))}</span></span>
        <span class="k-score" title="${esc(t("kurs.score_title"))}">${x.score}</span></button></li>`)
      .join("");
    const top3 = rows.slice(0, 3).map((x) => `<strong>${esc(x.name)}</strong>`);
    document.getElementById("k-chancen-insight").innerHTML = rows.length ? t("kurs.chancen_insight", { top: top3.join(", ") }) : "";
    document.getElementById("k-chancen-method").textContent = t("kurs.method_simple", { min: MIN_BASE }) +
      (skipped.length ? ` ${t("kurs.method_skipped", { list: skipped.map((x) => `${x.name} (${fmt.int(x.sample)})`).join(", ") })}` : "");
  }

  function syncUrl() {
    const url = new URL(location.href);
    url.searchParams.set("a", state.academy);
    if (state.title) url.searchParams.set("t", state.title);
    else url.searchParams.delete("t");
    if (state.onlyQe) url.searchParams.set("qe", "1");
    else url.searchParams.delete("qe");
    history.replaceState(null, "", url);
  }

  // Header: as-of date, sample and the source note.
  const total = D.academies.reduce((n, a) => n + a.sample, 0);
  document.getElementById("status").innerHTML =
    `<span class="status-dot" aria-hidden="true">●</span> ${esc(t("kurs.status", { date: fmt.date(D.as_of) }))}`;
  document.getElementById("k-note").innerHTML = t("kurs.note", {
    date: esc(fmt.date(D.as_of)), n: fmt.int(total), note: esc(D.note[lang] ?? D.note.de),
  });

  render();
  renderChancen();

  // Mark the section in view in the nav (the bottom tab bar on phones).
  const navLinks = [...document.querySelectorAll(".nav a[href^='#']")];
  const spy = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        navLinks.forEach((a) => (a.hash === `#${e.target.id}` ? a.setAttribute("aria-current", "location") : a.removeAttribute("aria-current")));
      }
    },
    { rootMargin: "-40% 0px -55% 0px" },
  );
  navLinks.forEach((a) => spy.observe(document.querySelector(a.hash)));
  spy.observe(document.querySelector(".hero"));
  const target = location.hash && document.getElementById(location.hash.slice(1));
  if (target) target.scrollIntoView({ behavior: "instant" });
}

main();
