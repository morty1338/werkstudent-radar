// "Which jobs fit you?": your skills (picked by hand or found in your CV),
// how many postings you match, what to learn next, and the postings themselves.
//
// A posting is a match when you have nearly all the skills it lists: you may
// miss one in four (rounded down), so 1–3 listed skills need all of them,
// 4–7 allow one gap, 8+ allow two.
//
// Each posting in the list also shows a match score (score.js: required skills
// 80 %, nice to have 20 %) and which of its skills you have, miss, or would be a plus.

import { esc, fmt } from "./charts.js?v=dev";
import { gapPlan } from "./gap.js?v=dev";
import { t } from "./i18n.js?v=dev";
import { scoreJob } from "./score.js?v=dev";
import { dataUrl, refPrefix } from "./mode.js?v=dev";

const POPULAR = 24;
const PAGE = 15;

export function initMatch(ctx, initialIds) {
  const { D, J } = ctx;
  const idxById = new Map(D.skills.map((s, i) => [s.id, i]));
  const byDemand = D.skills.map((_, i) => i).sort((a, b) => D.skills[b].jobs - D.skills[a].jobs);
  // Basic skills (MS Office, Excel, …) weigh less in the match score.
  const basic = new Set(D.skills.flatMap((s, i) => (ctx.BASICS?.has(s.id) ? [i] : [])));
  const selected = new Set(initialIds.filter((id) => idxById.has(id)).map((id) => idxById.get(id)));
  const st = { tab: null, sort: "best", shown: PAGE, search: "", showAll: false, mustSkill: null, cvFound: new Set(), open: false };
  let postings = null;
  let loading = null;
  let last = null;

  const el = {
    chips: document.getElementById("my-chips"),
    search: document.getElementById("my-search"),
    all: document.getElementById("my-all"),
    clear: document.getElementById("my-clear"),
    summary: document.getElementById("match-summary"),
    tabs: document.getElementById("job-tabs"),
    sort: document.getElementById("job-sort"),
    list: document.getElementById("job-list"),
    more: document.getElementById("job-more"),
    chip: document.getElementById("job-filter-chip"),
    panel: document.getElementById("jobs-panel"),
  };

  // --- Events ---------------------------------------------------------------------------
  el.search.addEventListener("input", () => { st.search = el.search.value.trim().toLowerCase(); renderChips(); });
  el.all.addEventListener("click", () => { st.showAll = !st.showAll; renderChips(); });
  el.clear.addEventListener("click", () => { selected.clear(); st.cvFound.clear(); changed(); });
  el.chips.addEventListener("click", (e) => {
    const c = e.target.closest("[data-skill]");
    if (c) toggleSkill(Number(c.dataset.skill));
  });
  el.summary.addEventListener("click", (e) => {
    const c = e.target.closest("[data-skill]");
    if (c) toggleSkill(Number(c.dataset.skill));
    if (e.target.closest("[data-act='jobs']")) {
      st.open = !st.open;
      update();
    }
  });
  el.tabs.addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]");
    if (!t) return;
    st.tab = t.dataset.tab;
    st.shown = PAGE;
    renderList();
  });
  el.sort.addEventListener("change", () => { st.sort = el.sort.value; renderList(); });
  el.more.addEventListener("click", () => { st.shown += PAGE; renderList(); });
  // "+ Track" adds the posting to the visitor's applications; once tracked it opens the list.
  el.list.addEventListener("click", (e) => {
    const b = e.target.closest("[data-track]");
    if (!b || !postings || !ctx.tracker) return;
    const job = posting(Number(b.dataset.track));
    if (ctx.tracker.isTracked(job.ref)) ctx.tracker.open(job.ref);
    else ctx.tracker.track(job);
  });
  ctx.tracker?.onChange(() => renderList());
  el.chip.addEventListener("click", (e) => {
    if (e.target.closest("button")) { st.mustSkill = null; renderList(); }
  });

  // Load the posting titles once the section comes into view.
  new IntersectionObserver((entries, obs) => {
    if (entries.some((e) => e.isIntersecting)) {
      ensurePostings();
      obs.disconnect();
    }
  }, { rootMargin: "600px" }).observe(document.getElementById("check"));

  function ensurePostings() {
    if (!loading) {
      loading = fetch(dataUrl("postings"), { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((data) => {
          // Both files come from the same build; otherwise positions don't line up.
          if (data.as_of !== D.as_of || data.rows.length !== D.jobs.length) throw new Error(t("check.updating"));
          postings = data;
          renderList();
        })
        .catch((err) => { el.list.innerHTML = `<p class="empty">${esc(t("check.load_error", { msg: err.message }))}</p>`; });
    }
    return loading;
  }

  // --- State changes ----------------------------------------------------------------------------
  function toggleSkill(i) {
    if (selected.has(i)) selected.delete(i);
    else selected.add(i);
    changed();
  }

  function setSkills(idxs, { fromCv = false } = {}) {
    selected.clear();
    idxs.forEach((i) => selected.add(i));
    st.cvFound = fromCv ? new Set(idxs) : new Set();
    st.tab = "match";
    if (fromCv) st.open = true;
    st.shown = PAGE;
    changed();
  }

  function showSkill(i) {
    st.mustSkill = i;
    st.tab = "all";
    st.shown = PAGE;
    st.open = true;
    update();
    el.panel.scrollIntoView({ behavior: "smooth", block: "start" });
    ensurePostings();
  }

  function changed() {
    if (!selected.size && st.tab !== "all") st.tab = null;
    update();
    ctx.onSkillsChanged?.();
  }

  // --- Matching ------------------------------------------------------------------------------------
  function evaluate() {
    const pool = ctx.pool();
    const listed = pool.filter((i) => D.jobs[i][J.SKILLS].length);
    const matched = [];
    const near = [];
    for (const i of listed) {
      const req = D.jobs[i][J.SKILLS];
      const missing = req.filter((s) => !selected.has(s));
      const allowed = Math.floor(req.length / 4);
      if (missing.length <= allowed) matched.push(i);
      else if (missing.length === allowed + 1) near.push([i, missing]);
    }
    // Skill gap: the next skills to learn, greedy by how many more postings each makes fit.
    const plan = selected.size
      ? gapPlan(listed.map((i) => D.jobs[i][J.SKILLS]), selected, { steps: 3, demand: D.skills.map((x) => x.jobs) })
      : [];
    return { pool, listed, matched, near, plan };
  }

  // --- Rendering -------------------------------------------------------------------------------------
  function renderChips() {
    const q = st.search;
    const visible = byDemand.filter((i) => {
      if (selected.has(i)) return true;
      const s = D.skills[i];
      if (q) return s.label.toLowerCase().includes(q) || s.id.includes(q);
      return st.showAll || byDemand.indexOf(i) < POPULAR;
    });
    // Your skills first, then the most asked-for ones.
    visible.sort((a, b) => (selected.has(b) - selected.has(a)) || D.skills[b].jobs - D.skills[a].jobs);
    el.chips.innerHTML = visible.length
      ? visible
          .map((i) => `<button type="button" class="chip${st.cvFound.has(i) ? " found" : ""}" data-skill="${i}" aria-pressed="${selected.has(i)}"
              title="${esc(t("check.chip_title", { n: fmt.int(D.skills[i].jobs) }))}">${esc(D.skills[i].label)}</button>`)
          .join("")
      : `<p class="empty">${esc(t("check.no_skill", { q }))}</p>`;
    el.all.textContent = st.showAll ? t("check.fewer") : t("check.all_skills", { n: fmt.int(D.skills.length) });
    el.all.hidden = Boolean(q);
    el.clear.hidden = !selected.size;
  }

  function renderSummary(r) {
    const toggle = (label) =>
      `<button type="button" class="btn${st.open ? " btn-quiet" : ""}" data-act="jobs" aria-expanded="${st.open}">${st.open ? t("check.hide_jobs") : label}</button>`;
    if (!selected.size) {
      el.summary.innerHTML = `<p class="match-empty">${esc(t("check.empty"))}</p>
        <div class="summary-actions">${toggle(t("check.browse", { n: fmt.int(r.pool.length) }))}</div>`;
      return;
    }
    const share = r.listed.length ? r.matched.length / r.listed.length : 0;
    el.summary.innerHTML = `
      <div class="match-big">${fmt.pct(share, 0)}</div>
      <div class="match-text">${t("check.fit", { n: fmt.int(r.matched.length), near: r.near.length ? t("check.near", { n: fmt.int(r.near.length) }) : "" })}</div>
      <div class="meter" aria-hidden="true"><div style="width:${share * 100}%"></div></div>
      ${r.plan.length ? gapText(r) : ""}
      <div class="summary-actions">${toggle(t("check.show_matching", { n: fmt.int(r.matched.length) }))}</div>`;
  }

  // "Learn SQL and Power BI to qualify for 48 more jobs", then the steps as chips to add.
  function gapText(r) {
    const two = r.plan.slice(0, 2);
    const names = two.map((p) => `<strong>${esc(D.skills[p.skill].label)}</strong>`).join(t("and"));
    const extra = two[two.length - 1].total;
    const steps = r.plan
      .map((p, k) => `${k ? '<span class="gap-then" aria-hidden="true">→</span>' : ""}<button type="button" class="chip add" data-skill="${p.skill}"
          title="${esc(t(k ? "check.gap_then" : "check.gap_first", { n: fmt.int(p.gain) }))}">${esc(D.skills[p.skill].label)} <small>+${fmt.int(p.gain)}</small></button>`)
      .join("");
    return `<div class="learn">
        <p class="gap-text">${t("check.gap", { names, n: fmt.int(extra), from: fmt.int(r.matched.length), to: fmt.int(r.matched.length + extra) })}</p>
        <span class="learn-label">${esc(t("check.learn_next"))}</span>${steps}
      </div>`;
  }

  function renderTabs(r) {
    if (!st.tab) st.tab = selected.size ? "match" : "all";
    const tabs = [
      ["match", t("check.tab_match", { n: fmt.int(r.matched.length) }), selected.size > 0],
      ["near", t("check.tab_near", { n: fmt.int(r.near.length) }), selected.size > 0],
      ["all", t("check.tab_all", { n: fmt.int(r.pool.length) }), true],
    ].filter((t) => t[2]);
    if (!tabs.some((t) => t[0] === st.tab)) st.tab = "all";
    el.tabs.innerHTML = tabs
      .map(([k, label]) => `<button type="button" class="tab" role="tab" data-tab="${k}" aria-selected="${st.tab === k}">${label}</button>`)
      .join("");
  }

  function renderList() {
    el.panel.hidden = !st.open;
    if (!last || !st.open) return;
    const r = last;
    renderTabs(r);
    el.chip.innerHTML = st.mustSkill != null
      ? `<span class="filter-chip">${esc(t("check.asking_for", { skill: D.skills[st.mustSkill].label }))} <button type="button" aria-label="${esc(t("check.remove"))}">×</button></span>`
      : "";
    if (!postings) {
      el.list.innerHTML = `<p class="empty">${esc(t("check.loading"))}</p>`;
      el.more.hidden = true;
      ensurePostings();
      return;
    }
    let items = st.tab === "match" ? r.matched.map((i) => [i, null]) : st.tab === "near" ? r.near : r.pool.map((i) => [i, null]);
    if (st.mustSkill != null) items = items.filter(([i]) => D.jobs[i][J.SKILLS].includes(st.mustSkill));
    const date = (i) => postings.rows[i][4] || "";
    // Best fit: the highest match score first; postings with too few skills for a
    // score after them. Ties: postings that ask for more of your skills first (a job
    // asking for Python, SQL and Power BI says more about you than one asking for MS Office).
    const scores = new Map();
    const score = (i) => {
      if (!scores.has(i)) scores.set(i, scoreJob(D.jobs[i][J.SKILLS], D.jobs[i][J.NICE], selected, basic));
      return scores.get(i);
    };
    const rank = (i) => score(i).score ?? -1;
    const fit = (i) => score(i).have.length;
    const byDate = (a, b) => date(b).localeCompare(date(a));
    const byPay = (a, b) => (D.jobs[b][J.PAY] ?? -1) - (D.jobs[a][J.PAY] ?? -1);
    const sort = st.sort === "best" && !selected.size ? "newest" : st.sort;
    items.sort(([a], [b]) =>
      sort === "pay" ? byPay(a, b) || byDate(a, b)
        : sort === "best" ? rank(b) - rank(a) || fit(b) - fit(a) || byPay(a, b) || byDate(a, b)
          : byDate(a, b),
    );
    el.list.innerHTML = items.length
      ? items.slice(0, st.shown).map(([i]) => row(i, selected.size ? score(i) : null)).join("")
      : `<p class="empty">${esc(t(st.tab === "match" ? "check.no_matches" : "check.no_jobs"))}</p>`;
    el.more.hidden = items.length <= st.shown;
    el.more.textContent = t("check.more", { n: fmt.int(items.length - st.shown) });
  }

  // Title, company, city and link of posting i, with its tracker key.
  function posting(i) {
    const [refnr, title, company, city, , link] = postings.rows[i];
    // Bundesagentur postings link via the reference number; company career sites have their own URL.
    const url = link || postings.url.replace("{refnr}", encodeURIComponent(refnr));
    return { ref: `${refPrefix.posting}${refnr}`, title, company, city, url };
  }

  function trackButton(ref, index) {
    const tracked = Boolean(ctx.tracker?.isTracked(ref));
    return `<button type="button" class="track-btn" data-track="${index}" aria-pressed="${tracked}"
      title="${esc(t(tracked ? "trk.tracked_title" : "trk.track_title"))}">${esc(t(tracked ? "trk.tracked" : "trk.track"))}</button>`;
  }

  // sc: scoreJob() of the posting against your skills, or null when none are picked.
  function row(i, sc) {
    const [, title, company, city, published] = postings.rows[i];
    const { ref, url } = posting(i);
    const j = D.jobs[i];
    const en = j[J.DE] <= 1 ? `<span class="badge en">${esc(t(j[J.DE] === 0 ? "check.no_german" : "check.german_plus"))}</span>` : "";
    return `<div class="job${sc ? " scored" : ""}">
      <a href="${esc(url)}" target="_blank" rel="noopener">${esc(title)}</a>
      <div class="meta">${esc(company)} · ${esc(city || "—")}${published ? ` · ${esc(fmt.shortDate(published))}` : ""}</div>
      ${sc ? `<div class="why-box">${explain(sc, j[J.LLM])}</div>` : ""}
      <div class="side">${sc ? scoreBadge(sc, j[J.LLM]) : ""}${j[J.PAY] != null ? `<span class="pay">${fmt.eur(j[J.PAY])}${t("check.per_hour")}</span>` : ""}${en}${trackButton(ref, i)}</div>
    </div>`;
  }

  // "82 % match", or "Little data" when the posting lists fewer than 2 skills or only basic ones.
  function scoreBadge(sc, llm) {
    if (sc.score == null) return `<span class="badge few" data-tip="${esc(t("check.few_data_tip"))}">${esc(t("check.few_data"))}</span>`;
    const tip = esc(t("check.score_tip")) + (llm ? "" : `<br>${esc(t("check.score_tip_rules"))}`);
    return `<span class="score" data-tip="${esc(tip)}">${esc(t("check.score", { pct: fmt.pct(sc.score, 0) }))}</span>`;
  }

  // Have / Missing (required) / Plus, each line only when it has skills. Without the
  // LLM's reading, the posting doesn't say what is required: the line is just "Missing".
  function explain(sc, llm) {
    const MAX = 5;
    // Specific skills first: "SAP · Controlling · Excel" says more than "Excel · SAP · Controlling".
    const names = (idxs) => [...idxs].sort((a, b) => basic.has(a) - basic.has(b)).slice(0, MAX).map((s) => esc(D.skills[s].label)).join(" · ")
      + (idxs.length > MAX ? ` <span class="why-more">${esc(t("check.why_more", { n: idxs.length - MAX }))}</span>` : "");
    const line = (cls, key, idxs) => idxs.length
      ? `<div class="why ${cls}"><span class="why-k">${esc(t(key))}</span> ${names(idxs)}</div>` : "";
    return line("have", "check.why_have", sc.have)
      + line("miss", llm ? "check.why_missing_must" : "check.why_missing", sc.missingMust)
      + line("plus", "check.why_plus", sc.missingNice);
  }

  function update() {
    last = evaluate();
    renderChips();
    renderSummary(last);
    renderList();
  }

  return {
    update,
    // postings.json (titles and links, by position), loaded on first use; null if it can't be.
    postings: () => ensurePostings().then(() => postings),
    toggleSkill,
    setSkills,
    showSkill,
    has: (i) => selected.has(i),
    ids: () => [...selected].map((i) => D.skills[i].id),
    headline: () => {
      if (!selected.size || !last) return null;
      const share = last.listed.length ? last.matched.length / last.listed.length : 0;
      return { value: fmt.pct(share, 0), sub: t("check.headline", { n: fmt.int(last.matched.length), k: selected.size }) };
    },
  };
}
