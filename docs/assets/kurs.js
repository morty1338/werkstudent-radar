// Kurs-Radar page (kurs.html): academy → job title → KPI tiles and skill bars, plus the
// "Opportunities" ranking. Data: data/kurs.json from radar/kurs_stats.py. No filters of
// the Werkstudent page apply here; the state is ?a=<academy>&t=<job title>.
import { esc, fmt, initTooltip, rollDigits } from "./charts.js?v=dev";
import { applyStatic, initLangSwitch, lang, skillLabel, t } from "./i18n.js?v=dev";
import { MIN_BASE, mustFraction, opportunities, shareOf, wowChange } from "./kurs-rank.js?v=dev";

async function loadKurs() {
  const res = await fetch("data/kurs.json", { cache: "no-cache" });
  if (!res.ok) throw new Error(`kurs.json: HTTP ${res.status}`);
  return res.json();
}

// --- Page --------------------------------------------------------------------------------------

async function main() {
  applyStatic();
  initLangSwitch();
  initTooltip();
  if (lang === "de") document.getElementById("k-home").href = "index.html?lang=de";
  let D;
  try {
    D = await loadKurs();
  } catch (e) {
    document.getElementById("k-tiles").innerHTML = `<p class="stale">${esc(t("load.error", { msg: e.message }))}</p>`;
    return;
  }
  const label = (id) => skillLabel(id, D.skills[id]?.label ?? id);
  const params = new URLSearchParams(location.search);
  const state = { academy: params.get("a"), title: params.get("t") };
  if (!D.academies.some((a) => a.id === state.academy)) state.academy = D.academies[0].id;

  const fAcademy = document.getElementById("k-academy");
  const fTitle = document.getElementById("k-title");
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
    render();
  }
  function setTitle(id) {
    state.title = id || null;
    render();
  }
  fAcademy.addEventListener("change", () => setAcademy(fAcademy.value));
  fTitle.addEventListener("change", () => setTitle(fTitle.value));
  document.getElementById("k-academies").addEventListener("click", (e) => {
    const b = e.target.closest("[data-academy]");
    if (b) setAcademy(b.dataset.academy);
  });
  document.getElementById("k-titles").addEventListener("click", (e) => {
    const b = e.target.closest("[data-title]");
    if (b) setTitle(b.dataset.title);
  });
  const compare = document.getElementById("k-compare");
  compare.addEventListener("click", (e) => {
    const r = e.target.closest("[data-title]");
    if (r) setTitle(r.dataset.title === state.title ? "" : r.dataset.title);
  });
  compare.addEventListener("keydown", (e) => {
    const r = e.target.closest("[data-title]");
    if (r && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      setTitle(r.dataset.title === state.title ? "" : r.dataset.title);
    }
  });
  document.getElementById("k-chancen").addEventListener("click", (e) => {
    const r = e.target.closest("[data-pick]");
    if (!r) return;
    const [a, ti] = r.dataset.pick.split("/");
    state.academy = a;
    state.title = ti;
    render();
    document.getElementById("jobs").scrollIntoView({ behavior: "smooth" });
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

  function render() {
    const { a, ti, view } = current();
    renderFilters(a, ti);
    renderHeading(a, ti, view);
    renderTiles(view);
    renderSkills(view);
    renderCompare(a, ti);
    renderTerms(view);
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

  const count = (x) => `${x.postings_exact ? "" : "≥ "}${fmt.int(x.postings)}`;
  const pctOr = (m) => (shareOf(m) == null ? "—" : fmt.pct(m.share, 0));

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
    const wow = wowChange(view.wow);
    const tiles = [
      {
        label: t("kurs.tile_postings"),
        value: count(view),
        sub: wow != null
          ? t("kurs.tile_wow", { pct: fmt.signedPct(wow), date: fmt.shortDate(view.wow.before_date) })
          : t("kurs.tile_new7", { pct: fmt.pct(view.new_7d.share ?? 0, 0) }),
        tip: view.postings_exact ? t("kurs.tip_exact") : t("kurs.tip_estimate", { sample: fmt.int(view.sample) }),
      },
      {
        label: t("kurs.tile_qe"),
        value: pctOr(view.quereinsteiger),
        sub: shareOf(view.quereinsteiger) == null ? few(view.quereinsteiger)
          : t("kurs.tile_of", { n: fmt.int(view.quereinsteiger.n), base: fmt.int(view.quereinsteiger.base) }),
        tip: t("kurs.tip_qe", { text: fmt.int(view.quereinsteiger.text), ba: fmt.int(view.quereinsteiger.ba_flag) }),
      },
      {
        label: t("kurs.tile_ai"),
        value: pctOr(view.ai),
        sub: shareOf(view.ai) == null ? few(view.ai) : t("kurs.tile_of", { n: fmt.int(view.ai.n), base: fmt.int(view.ai.base) }),
        tip: t("kurs.tip_ai"),
      },
      {
        label: t("kurs.tile_basics"),
        value: pctOr(view.basics),
        sub: shareOf(view.basics) == null ? few(view.basics) : t("kurs.tile_basics_sub"),
        tip: t("kurs.tip_basics"),
      },
    ];
    const box = document.getElementById("k-tiles");
    const before = [...box.querySelectorAll(".value")].map((v) => v.dataset.value);
    box.innerHTML = tiles
      .map((x) => `<div class="tile" data-tip="${esc(x.tip)}">
        <span class="label">${esc(x.label)}</span>
        <span class="value" data-value="${esc(x.value)}">${esc(x.value)}</span>
        <span class="sub">${esc(x.sub)}</span></div>`)
      .join("");
    box.querySelectorAll(".value").forEach((v, k) => {
      if (v.dataset.value !== before[k]) rollDigits(v, before[k]);
    });
  }

  function renderSkills(view) {
    const el = document.getElementById("k-skills");
    const rows = view.skills;
    if (!rows.length || view.with_skills < MIN_BASE) {
      el.innerHTML = `<p class="empty">${esc(t("kurs.skills_few", { n: fmt.int(view.with_skills) }))}</p>`;
    } else {
      const top = Math.max(...rows.map((s) => s.share), 0.01);
      el.innerHTML = `<div class="bars">${rows
        .map((s) => {
          const w = Math.max(0.6, (s.share / top) * 100);
          const m = mustFraction(s);
          const fill = m == null
            ? `<div class="bar-fill" style="width:${w.toFixed(1)}%"></div>`
            : `<div class="bar-split" style="width:${w.toFixed(1)}%"><span class="f-must" style="width:${(m * 100).toFixed(1)}%"></span><span class="f-nice"></span></div>`;
          const tip = `<strong>${esc(label(s.id))}</strong><br>${esc(t("kurs.bar_tip", { n: fmt.int(s.n), base: fmt.int(view.with_skills) }))}` +
            (m == null ? "" : `<br>${esc(t("kurs.bar_tip_imp", { must: fmt.int(s.must), nice: fmt.int(s.nice) }))}`);
          return `<div class="bar-row" data-tip="${esc(tip)}">
            <div class="bar-label">${esc(label(s.id))}</div>
            <div class="bar-cell"><div class="bar-track">${fill}</div><span class="bar-value">${fmt.pct(s.share, s.share < 0.1 ? 1 : 0)}</span></div>
          </div>`;
        })
        .join("")}</div>`;
    }
    const basics = view.basics_skills;
    document.getElementById("k-basics").innerHTML = basics.length && view.with_skills >= MIN_BASE
      ? `<span class="basics-label" data-tip="${esc(t("kurs.basics_tip"))}">${esc(t("skills.basics"))}</span>` +
        basics.map((s) => `<span class="chip chip-static" data-tip="${esc(t("kurs.bar_tip", { n: fmt.int(s.n), base: fmt.int(view.with_skills) }))}">${esc(label(s.id))} <span class="chip-n">${fmt.pct(s.share, s.share < 0.1 ? 1 : 0)}</span></span>`).join("")
      : "";
    document.getElementById("k-skills-note").textContent = t("kurs.skills_note", {
      n: fmt.int(view.with_skills), llm: fmt.int(view.read_by_llm),
    }) + (view.postings_exact ? "" : ` ${t("kurs.sample_note", { sample: fmt.int(view.sample) })}`);
  }

  function renderCompare(a, ti) {
    document.getElementById("k-side-title").textContent = t("kurs.compare");
    const head = `<div class="k-row k-head" aria-hidden="true"><span class="k-name">${esc(t("kurs.col_title"))}</span><span>${esc(t("kurs.col_postings"))}</span>` +
      `<span>${esc(t("kurs.col_qe"))}</span><span>${esc(t("kurs.col_ai"))}</span><span>${esc(t("kurs.col_basics"))}</span></div>`;
    compare.innerHTML = `<div class="k-table">${head}${a.titles
      .map((x) => `<div class="k-row clickable${x.id === ti?.id ? " selected" : ""}" role="button" tabindex="0" data-title="${esc(x.id)}" aria-pressed="${x.id === ti?.id}"
          data-tip="${esc(t("kurs.row_aria", { name: x.name, n: count(x), qe: pctOr(x.quereinsteiger), ai: pctOr(x.ai), basics: pctOr(x.basics) }))}">
        <span class="k-name">${esc(x.name)}</span><span>${esc(count(x))}</span>
        <span>${pctOr(x.quereinsteiger)}</span><span>${pctOr(x.ai)}</span><span>${pctOr(x.basics)}</span></div>`)
      .join("")}</div>`;
  }

  function renderTerms(view) {
    const terms = view.other_terms ?? [];
    document.getElementById("k-terms").innerHTML = terms.length
      ? terms.map((x) => `<span class="chip chip-static">${esc(x.term)} <span class="chip-n">${fmt.int(x.n)}</span></span>`).join("")
      : `<p class="empty">${esc(t("kurs.terms_none"))}</p>`;
  }

  function renderChancen() {
    const { rows, skipped, momentum } = opportunities(D);
    const max = { postings: Math.max(...rows.map((x) => x.postings), 1), m: Math.max(...rows.map((x) => Math.abs(x.momentum)), 0.01),
      qe: Math.max(...rows.map((x) => x.quereinsteiger.share), 0.01) };
    const bar = (v, top) => `<span class="mini"><span class="mini-fill" style="width:${Math.max(2, (Math.abs(v) / top) * 100).toFixed(1)}%"></span></span>`;
    // Postings span 2 to 14,000: a log scale keeps the small titles visible.
    const logBar = (v) => bar(Math.log10(Math.max(v, 1)), Math.log10(Math.max(max.postings, 10)));
    const mLabel = momentum === "wow" ? t("kurs.col_wow") : t("kurs.col_new7");
    const head = `<div class="ch-row ch-head" aria-hidden="true"><span>${esc(t("kurs.col_title"))}</span><span>${esc(t("kurs.col_postings"))}</span>` +
      `<span>${esc(mLabel)}</span><span>${esc(t("kurs.col_qe"))}</span><span>${esc(t("kurs.col_score"))}</span></div>`;
    document.getElementById("k-chancen").innerHTML = `<div class="ch-table">${head}${rows
      .map((x, i) => `<div class="ch-row clickable${i < 3 ? " top" : ""}" role="button" tabindex="0" data-pick="${esc(x.academy.id)}/${esc(x.id)}">
        <span class="ch-name"><strong>${esc(x.name)}</strong> <small>${esc(x.academy.name)}</small></span>
        <span class="ch-cell"><em>${esc(t("kurs.col_postings"))}</em>${logBar(x.postings)}<b>${esc(count(x))}</b></span>
        <span class="ch-cell"><em>${esc(mLabel)}</em>${bar(x.momentum, max.m)}<b>${momentum === "wow" ? fmt.signedPct(x.momentum) : fmt.pct(x.momentum, 0)}</b></span>
        <span class="ch-cell"><em>${esc(t("kurs.col_qe"))}</em>${bar(x.quereinsteiger.share, max.qe)}<b>${fmt.pct(x.quereinsteiger.share, 0)}</b></span>
        <span class="ch-score"><em>${esc(t("kurs.col_score"))}</em><b>${x.score}</b></span>
      </div>`)
      .join("")}</div>`;
    document.getElementById("k-chancen").onkeydown = (e) => {
      const r = e.target.closest("[data-pick]");
      if (r && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        r.click();
      }
    };
    const top3 = rows.slice(0, 3).map((x) => `<strong>${esc(x.name)}</strong>`);
    document.getElementById("k-chancen-insight").innerHTML = rows.length
      ? t(momentum === "wow" ? "kurs.chancen_insight_wow" : "kurs.chancen_insight", { top: top3.join(", ") })
      : "";
    document.getElementById("k-chancen-method").textContent =
      t(momentum === "wow" ? "kurs.method_wow" : "kurs.method_new7", { n: rows.length, min: MIN_BASE }) +
      (skipped.length ? ` ${t("kurs.method_skipped", { list: skipped.map((x) => `${x.name} (${fmt.int(x.sample)})`).join(", ") })}` : "");
  }

  function syncUrl() {
    const url = new URL(location.href);
    url.searchParams.set("a", state.academy);
    if (state.title) url.searchParams.set("t", state.title);
    else url.searchParams.delete("t");
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
