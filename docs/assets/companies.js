// "Company career pages": Werkstudent jobs in Berlin and München read every day
// from the companies' own job boards (Greenhouse, Lever, Ashby, Personio), from
// data/companies.json. A city switch, field chips and a keyword search; jobs are
// grouped by company (fresh ones first) and link to the original posting. Not tied to the filter bar,
// which covers all of Germany. Remote jobs in Germany show in both cities.

import { esc, fmt } from "./charts.js?v=dev";
import { fieldLabel, lang, t } from "./i18n.js?v=dev";
import { refPrefix } from "./mode.js?v=dev";

const CITIES = ["Berlin", "München"];
const CITY_PARAM = { Berlin: "berlin", München: "muenchen" };

export function initCompanies(ctx, data) {
  const section = document.getElementById("companies");
  const navLink = document.querySelector('.nav a[href="#companies"]');
  if (!data || !Array.isArray(data.jobs)) {
    section.hidden = true;
    if (navLink) navLink.hidden = true;
    return;
  }

  const params = new URLSearchParams(location.search);
  const fromUrl = CITIES.find((c) => CITY_PARAM[c] === params.get("co"));
  const st = { city: fromUrl ?? "Berlin", field: null, q: "" };
  const el = {
    insight: document.getElementById("co-insight"),
    city: document.getElementById("co-city"),
    search: document.getElementById("co-search"),
    fields: document.getElementById("co-fields"),
    list: document.getElementById("co-list"),
    note: document.getElementById("co-note"),
  };
  const companies = new Map((data.companies || []).map((c) => [c.name, c]));
  const label = (id) => fieldLabel(id, data.field_labels?.[id] ?? id);
  const berlinToday = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Berlin" });
  const fresh = data.as_of === berlinToday;
  const inCity = (j, city) => j.city === city || j.city === "";

  // URL: ?co=muenchen (Berlin is the default); app.js writes it with the other filters.
  ctx.companiesParam = () => (st.city === "Berlin" ? null : CITY_PARAM[st.city]);

  el.city.addEventListener("click", (e) => {
    const b = e.target.closest("[data-city]");
    if (!b || b.dataset.city === st.city) return;
    st.city = b.dataset.city;
    st.field = null;
    render();
    ctx.syncUrl();
  });
  el.fields.addEventListener("click", (e) => {
    const b = e.target.closest("[data-field]");
    if (!b) return;
    st.field = b.dataset.field === st.field || b.dataset.field === "" ? null : b.dataset.field;
    render();
  });
  // "+ Track" adds a job to the visitor's applications; once tracked it opens the list.
  const index = new Map(data.jobs.map((j, i) => [j, i]));
  const jobRef = (j) => `${refPrefix.company}${j.url}`;
  el.list.addEventListener("click", (e) => {
    const b = e.target.closest("[data-track]");
    if (!b || !ctx.tracker) return;
    const j = data.jobs[Number(b.dataset.track)];
    if (ctx.tracker.isTracked(jobRef(j))) ctx.tracker.open(jobRef(j));
    else ctx.tracker.track({ ref: jobRef(j), title: j.title, company: j.company, city: j.city || t("co.remote_de"), url: j.url });
  });
  ctx.tracker?.onChange(() => render());
  ctx.companiesUrls = () => new Set(data.jobs.map((j) => j.url));

  el.search.addEventListener("input", () => {
    st.q = el.search.value.trim().toLowerCase();
    render();
  });

  const matchesQuery = (j) => {
    if (!st.q) return true;
    const text = `${j.title} ${j.company} ${label(j.field)}`.toLowerCase();
    return st.q.split(/\s+/).every((w) => text.includes(w));
  };

  function render() {
    // City switch with counts.
    el.city.innerHTML = CITIES.map((c) => {
      const n = data.jobs.filter((j) => inCity(j, c)).length;
      return `<button type="button" role="tab" data-city="${esc(c)}" aria-selected="${c === st.city}">${esc(c)} <span class="seg-n">${fmt.int(n)}</span></button>`;
    }).join("");

    const pool = data.jobs.filter((j) => inCity(j, st.city) && matchesQuery(j));
    // Field chips: the fields present in this city (and search), most jobs first.
    const counts = new Map();
    for (const j of pool) counts.set(j.field, (counts.get(j.field) || 0) + 1);
    if (st.field && !counts.has(st.field)) st.field = null;
    const fields = [...counts].sort((a, b) => (a[0] === "other") - (b[0] === "other") || b[1] - a[1] || label(a[0]).localeCompare(label(b[0]), lang));
    el.fields.innerHTML = pool.length
      ? [`<button type="button" class="chip" data-field="" aria-pressed="${st.field === null}">${esc(t("co.all_fields"))} <span class="chip-n">${fmt.int(pool.length)}</span></button>`]
          .concat(fields.map(([id, n]) => `<button type="button" class="chip" data-field="${esc(id)}" aria-pressed="${st.field === id}">${esc(label(id))} <span class="chip-n">${fmt.int(n)}</span></button>`))
          .join("")
      : "";

    const shown = pool.filter((j) => !st.field || j.field === st.field);
    const groups = new Map();
    for (const j of shown) {
      if (!groups.has(j.company)) groups.set(j.company, []);
      groups.get(j.company).push(j);
    }
    // Companies with jobs new today first, then by their latest posting; newest jobs first within.
    const latest = (jobs) => jobs.reduce((m, j) => ((j.posted_at || j.first_seen) > m ? j.posted_at || j.first_seen : m), "");
    for (const jobs of groups.values()) jobs.sort((a, b) => b.new - a.new || (b.posted_at || "").localeCompare(a.posted_at || ""));
    const order = [...groups].sort((a, b) =>
      b[1].some((j) => j.new) - a[1].some((j) => j.new) || latest(b[1]).localeCompare(latest(a[1])) || a[0].localeCompare(b[0], lang));
    el.list.innerHTML = order.length
      ? order.map(([name, jobs]) => group(name, jobs)).join("")
      : `<p class="co-empty">${esc(st.q || st.field ? t("co.no_match") : t("co.empty", { city: st.city }))}</p>`;

    const all = data.jobs.filter((j) => inCity(j, st.city));
    const n = all.length;
    const firms = new Set(all.map((j) => j.company)).size;
    const isNew = all.filter((j) => j.new).length;
    const onBa = all.filter((j) => j.also_on?.includes("ba")).length;
    el.insight.innerHTML = n
      ? t("co.insight", { n: fmt.int(n), firms: fmt.int(firms), city: esc(st.city) }) +
        (isNew ? ` ${t(fresh ? "co.insight_new" : "co.insight_new_on", { n: fmt.int(isNew), date: fmt.shortDate(data.as_of) })}` : "") +
        ` ${t("co.insight_only", { n: fmt.int(n - onBa) })}`
      : esc(t("co.empty", { city: st.city }));

    const boards = [...companies.values()];
    const read = boards.filter((c) => c.ok).length;
    const quiet = boards.filter((c) => c.cities.includes(st.city) && !all.some((j) => j.company === c.name)).map((c) => c.name);
    el.note.innerHTML = t("co.note", { n: fmt.int(boards.length), read: fmt.int(read), date: fmt.date(data.as_of) }) +
      (quiet.length ? ` ${t("co.quiet", { list: quiet.map(esc).join(", ") })}` : "");
  }

  function group(name, jobs) {
    const c = companies.get(name) || {};
    const link = c.careers_url
      ? `<a class="co-link" href="${esc(c.careers_url)}" target="_blank" rel="noopener">${esc(t("co.careers"))}</a>`
      : "";
    return `<div class="co-group">
      <div class="co-head"><h3>${esc(name)}</h3><span class="co-count">${fmt.int(jobs.length)}</span>${link}</div>
      <div class="job-list">${jobs.map(row).join("")}</div>
    </div>`;
  }

  function trackButton(j) {
    const tracked = Boolean(ctx.tracker?.isTracked(jobRef(j)));
    return `<button type="button" class="track-btn" data-track="${index.get(j)}" aria-pressed="${tracked}"
      title="${esc(t(tracked ? "trk.tracked_title" : "trk.track_title"))}">${esc(t(tracked ? "trk.tracked" : "trk.track"))}</button>`;
  }

  function row(j) {
    const where = j.city || t("co.remote_de");
    const meta = [label(j.field), where, j.posted_at ? t("co.posted", { date: fmt.date(j.posted_at) }) : null].filter(Boolean).map(esc).join(" · ");
    const badges = [
      j.new ? `<span class="badge new" title="${esc(t("co.first_seen", { date: fmt.date(j.first_seen) }))}">${esc(fresh ? t("co.new_today") : t("co.new_on", { date: fmt.shortDate(j.first_seen) }))}</span>` : "",
      j.remote ? `<span class="badge">${esc(t("co.remote"))}</span>` : "",
      j.also_on?.includes("ba") ? `<span class="badge" title="${esc(t("co.also_ba_title"))}">${esc(t("co.also_ba"))}</span>` : "",
      trackButton(j),
    ].join("");
    return `<div class="job">
      <a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>
      <div class="meta">${meta}</div>
      <div class="side">${badges}</div>
    </div>`;
  }

  render();
}
