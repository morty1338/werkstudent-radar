// "Applications": the visitor's own application tracker, a panel that slides in from the
// right (full screen on phones) behind the header button #trk-open. The list lives only in
// this browser (tracker-store.js); export/import files are the backup. Job lists add
// postings with track() (key `ref`: "p:<refnr>" or "c:<url>"); setOnlineCheck() lets the
// panel mark tracked postings that are no longer in today's data.

import { esc, fmt } from "./charts.js?v=dev";
import { lang, t } from "./i18n.js?v=dev";
import {
  BACKUP_EVERY_DAYS, KEY, STATUSES, counts, createStore, daysSinceBackup, fromLinkData, isOverdue, markBackup, sorted, toLinkData, today,
} from "./tracker-store.js?v=dev";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function initTracker() {
  const el = {
    open: document.getElementById("trk-open"),
    count: document.getElementById("trk-count"),
    panel: document.getElementById("trk"),
    backdrop: document.getElementById("trk-backdrop"),
    close: document.getElementById("trk-close"),
    local: document.getElementById("trk-local"),
    add: document.getElementById("trk-new"),
    form: document.getElementById("trk-form"),
    cancel: document.getElementById("trk-cancel"),
    filter: document.getElementById("trk-filter"),
    list: document.getElementById("trk-list"),
    exportBtn: document.getElementById("trk-export"),
    linkBtn: document.getElementById("trk-link"),
    importInput: document.getElementById("trk-import"),
    msg: document.getElementById("trk-msg"),
  };
  const listeners = new Set();
  const changed = () => {
    render();
    listeners.forEach((fn) => fn());
  };
  let store = createStore(storage(), { onChange: changed });
  const st = { filter: "all", confirmDelete: null, gone: new Set(), onlineCheck: null };
  const label = (s) => t(`trk.s_${s}`);

  // --- Opening and closing ---------------------------------------------------------------------
  function open(ref) {
    el.panel.classList.add("open");
    el.panel.inert = false;
    el.backdrop.hidden = false;
    el.open.setAttribute("aria-expanded", "true");
    document.body.classList.add("trk-open");
    const a = ref ? store.findByRef(ref) : null;
    if (a) st.filter = "all";
    render();
    el.close.focus();
    if (a) el.list.querySelector(`[data-id="${CSS.escape(a.id)}"]`)?.scrollIntoView({ block: "center" });
    refreshOnline();
  }

  // Which tracked postings from the radar have left today's data (asked each time the panel opens).
  async function refreshOnline() {
    const refs = store.all().filter((a) => a.source === "radar" && a.ref).map((a) => a.ref);
    if (!st.onlineCheck || !refs.length) return;
    try {
      st.gone = await st.onlineCheck(refs);
      render();
    } catch {
      // Unknown today; no marks rather than wrong ones.
    }
  }
  function close() {
    el.panel.classList.remove("open");
    el.panel.inert = true;
    el.backdrop.hidden = true;
    el.open.setAttribute("aria-expanded", "false");
    document.body.classList.remove("trk-open");
    el.open.focus();
  }
  el.panel.inert = true;
  el.open.addEventListener("click", open);
  el.close.addEventListener("click", close);
  el.backdrop.addEventListener("click", close);
  el.panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      close();
      return;
    }
    if (e.key !== "Tab") return;
    // Keep the focus inside the panel while it is open.
    const items = [...el.panel.querySelectorAll("a[href], button, input, select, textarea, summary")]
      .filter((x) => !x.disabled && x.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });

  // --- New application (a job found anywhere) ------------------------------------------------
  el.add.addEventListener("click", () => {
    el.form.hidden = !el.form.hidden;
    el.add.setAttribute("aria-expanded", String(!el.form.hidden));
    if (!el.form.hidden) el.form.elements.title.focus();
  });
  el.cancel.addEventListener("click", () => {
    el.form.reset();
    el.form.hidden = true;
    el.add.setAttribute("aria-expanded", "false");
  });
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = el.form.elements;
    const a = store.add({
      title: f.title.value, company: f.company.value, city: f.city.value, url: f.url.value,
      status: f.status.value, note: f.note.value, source: "manual",
    });
    if (!a) return;
    el.form.reset();
    el.form.hidden = true;
    el.add.setAttribute("aria-expanded", "false");
    st.filter = "all";
    render();
    say(t("trk.added", { title: a.title }));
  });

  // --- Editing ----------------------------------------------------------------------------------
  el.list.addEventListener("change", (e) => {
    const card = e.target.closest("[data-id]");
    if (!card) return;
    const id = card.dataset.id;
    const act = e.target.dataset.act;
    if (act === "status") store.setStatus(id, e.target.value);
    if (act === "follow") store.update(id, { followUp: e.target.value });
    if (act === "note") store.update(id, { note: e.target.value });
  });
  // A note is saved while typing (closing the tab right after keeps it), without a redraw.
  el.list.addEventListener("input", (e) => {
    if (e.target.dataset.act !== "note") return;
    store.update(e.target.closest("[data-id]").dataset.id, { note: e.target.value }, { quiet: true });
  });
  el.list.addEventListener("click", (e) => {
    const button = e.target.closest("[data-act='delete']");
    if (!button) return;
    const id = button.closest("[data-id]").dataset.id;
    if (st.confirmDelete === id) {
      st.confirmDelete = null;
      store.remove(id);
      say(t("trk.deleted"));
    } else {
      st.confirmDelete = id;    // second click within a few seconds deletes
      render();
      setTimeout(() => {
        if (st.confirmDelete === id) {
          st.confirmDelete = null;
          render();
        }
      }, 4000);
    }
  });
  el.filter.addEventListener("click", (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    st.filter = b.dataset.filter;
    render();
  });

  // --- Export and import ------------------------------------------------------------------------
  el.exportBtn.addEventListener("click", () => {
    const blob = new Blob([store.exportJson()], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `werkstudent-radar-applications-${today()}.json`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    markBackup(storage());
    render();
  });

  // Backup link: the list in the link itself (after #, never sent to the server). As a bookmark it
  // survives a browser that clears site data (Safari after 7 days without a visit); opened on
  // another device it imports the list.
  el.linkBtn?.addEventListener("click", async () => {
    let url;
    try {
      url = `${location.origin}${location.pathname}${lang === "de" ? "?lang=de" : ""}#trk=${await toLinkData(store.exportJson())}`;
    } catch {
      say(t("trk.link_failed"));
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      say(t("trk.link_copied"));
    } catch {
      window.prompt(t("trk.link_prompt"), url);           // no clipboard access: copy by hand
    }
    markBackup(storage());
    render();
  });
  async function importFromLink() {
    if (!location.hash.startsWith("#trk=")) return;
    const data = location.hash.slice(5);
    history.replaceState(null, "", location.pathname + location.search);   // the link isn't kept in the address
    try {
      const { added, updated } = store.importJson(await fromLinkData(data));
      markBackup(storage());
      open();
      say(t("trk.imported", { added, updated }));
    } catch (err) {
      open();
      say(t("trk.import_failed", { msg: err.message }));
    }
  }

  // Ask the browser to keep the list even when disk space runs low (Chrome, Firefox, Safari 17+
  // decide themselves; nothing is shown to the visitor).
  let persistAsked = false;
  function keepStorage() {
    if (persistAsked || !store.all().length) return;
    persistAsked = true;
    navigator.storage?.persist?.().catch(() => {});
  }
  el.importInput.addEventListener("change", async () => {
    const file = el.importInput.files[0];
    if (!file) return;
    try {
      const { added, updated } = store.importJson(await file.text());
      say(t("trk.imported", { added, updated }));
    } catch (err) {
      say(t("trk.import_failed", { msg: err.message }));
    } finally {
      el.importInput.value = "";
    }
  });

  // Another tab changed the list: read it again.
  window.addEventListener("storage", (e) => {
    if (e.key !== KEY) return;
    store = createStore(storage(), { onChange: changed });
    changed();
  });

  function say(message) {
    el.msg.textContent = message;
    setTimeout(() => {
      if (el.msg.textContent === message) el.msg.textContent = "";
    }, 4000);
  }

  // --- Drawing -----------------------------------------------------------------------------------
  function render() {
    const day = today();
    const items = store.all();
    const c = counts(items, day);
    el.count.textContent = String(c.all);
    el.count.hidden = c.all === 0;
    el.open.classList.toggle("overdue", c.overdue > 0);
    el.open.setAttribute("aria-label", t("trk.button_aria", { n: c.all, overdue: c.overdue }));
    // Browsers may clear site data (Safari after 7 days without a visit): ask for a backup now and then.
    const since = daysSinceBackup(storage(), day);
    const due = store.persistent && c.all > 0 && (since == null || since >= BACKUP_EVERY_DAYS);
    el.local.textContent = !store.persistent ? t("trk.memory") : due ? t("trk.backup_due") : t("trk.local");
    el.local.classList.toggle("warn", !store.persistent || due);
    keepStorage();
    if (!el.panel.classList.contains("open")) return;

    const filters = [["all", t("trk.all"), c.all], ...STATUSES.map((s) => [s, label(s), c[s]])];
    if (c.overdue) filters.splice(1, 0, ["overdue", t("trk.overdue"), c.overdue]);
    if (!filters.some(([k]) => k === st.filter)) st.filter = "all";
    el.filter.innerHTML = items.length
      ? filters.filter(([k, , n]) => k === "all" || n > 0)
          .map(([k, name, n]) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${st.filter === k}">${esc(name)} <span class="chip-n">${n}</span></button>`)
          .join("")
      : "";

    const shown = sorted(items, day).filter((a) =>
      st.filter === "all" || (st.filter === "overdue" ? isOverdue(a, day) : a.status === st.filter));
    el.list.innerHTML = shown.length
      ? shown.map((a) => card(a, day)).join("")
      : `<p class="trk-empty">${esc(items.length ? t("trk.empty_filter") : t("trk.empty"))}</p>`;
  }

  function card(a, day) {
    const title = a.url
      ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>`
      : `<strong>${esc(a.title)}</strong>`;
    const meta = [a.company, a.city, a.source === "radar" ? t("trk.from_radar") : null].filter(Boolean).map(esc).join(" · ");
    const history = a.history.map((h) => `${esc(label(h.status))} ${esc(fmt.shortDate(h.date))}`).join(" → ");
    const options = STATUSES.map((s) => `<option value="${s}"${s === a.status ? " selected" : ""}>${esc(label(s))}</option>`).join("");
    const overdue = isOverdue(a, day) ? `<span class="badge overdue">${esc(t("trk.overdue"))}</span>` : "";
    const gone = st.gone.has(a.ref) ? `<span class="badge gone" title="${esc(t("trk.gone_title"))}">${esc(t("trk.gone"))}</span>` : "";
    const confirming = st.confirmDelete === a.id;
    return `<article class="trk-item${a.status === "rejected" ? " closed" : ""}" data-id="${esc(a.id)}">
      <div class="trk-item-head">${title}<span class="trk-badges">${overdue}${gone}</span></div>
      ${meta ? `<div class="meta">${meta}</div>` : ""}
      <div class="trk-fields">
        <label><span>${esc(t("trk.f_status"))}</span><select data-act="status">${options}</select></label>
        <label><span>${esc(t("trk.f_follow"))}</span><input type="date" data-act="follow" value="${esc(a.followUp ?? "")}"></label>
      </div>
      <div class="trk-history">${history}</div>
      <label class="trk-note"><span>${esc(t("trk.f_note"))}</span><textarea data-act="note" rows="2" maxlength="2000">${esc(a.note)}</textarea></label>
      <button type="button" class="link-btn trk-delete${confirming ? " confirm" : ""}" data-act="delete">${esc(confirming ? t("trk.delete_confirm") : t("trk.delete"))}</button>
    </article>`;
  }

  render();
  importFromLink();
  return {
    open,
    isTracked: (ref) => Boolean(store.findByRef(ref)),
    // A posting from one of the site's job lists: { ref, title, company, city, url }.
    track(job) {
      const a = store.add({ ...job, source: "radar", status: "saved" });
      if (a) say(t("trk.added", { title: a.title }));
      return a;
    },
    onChange: (fn) => listeners.add(fn),
    // fn(refs) resolves to the set of refs that are no longer online.
    setOnlineCheck(fn) {
      st.onlineCheck = fn;
    },
  };
}
