// Application tracker: the data and its rules, no DOM (tested in Node).
//
// Everything stays in the visitor's browser (localStorage, key KEY); an export
// file is the backup and the way to move the list to another device. An
// application: { id, title, company, city, url, source ("radar" | "manual"),
// ref (posting id on the radar, or null), status, history: [{ status, date }],
// note, followUp ("YYYY-MM-DD" or null), added }. Dates are local calendar days.

export const KEY = "werkstudent-radar.applications";
export const VERSION = 1;
export const STATUSES = ["saved", "applied", "interview", "offer", "rejected"];
const OPEN = new Set(["saved", "applied", "interview"]);   // still waiting for something
const MAX_TEXT = { title: 200, company: 120, city: 80, url: 500, note: 2000 };

export function today(now = new Date()) {
  const d = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 10);
}

const isDay = (s) => typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s);
const text = (v, max) => (typeof v === "string" ? v.trim().slice(0, max) : "");
const safeUrl = (u) => {
  const s = text(u, MAX_TEXT.url);
  return /^https?:\/\//i.test(s) ? s : "";
};
const newId = () => `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

// One application from untrusted input (a form, a file); null if it has no title.
export function clean(a, day = today()) {
  if (!a || typeof a !== "object") return null;
  const title = text(a.title, MAX_TEXT.title);
  if (!title) return null;
  const status = STATUSES.includes(a.status) ? a.status : "saved";
  const history = (Array.isArray(a.history) ? a.history : [])
    .filter((h) => h && STATUSES.includes(h.status) && isDay(h.date))
    .map((h) => ({ status: h.status, date: h.date }));
  if (!history.length || history[history.length - 1].status !== status) history.push({ status, date: day });
  return {
    id: typeof a.id === "string" && /^[\w-]{1,40}$/.test(a.id) ? a.id : newId(),
    title,
    company: text(a.company, MAX_TEXT.company),
    city: text(a.city, MAX_TEXT.city),
    url: safeUrl(a.url),
    source: a.source === "radar" ? "radar" : "manual",
    ref: typeof a.ref === "string" ? a.ref.slice(0, 200) : null,
    status,
    history,
    note: text(a.note, MAX_TEXT.note),
    followUp: isDay(a.followUp) ? a.followUp : null,
    added: isDay(a.added) ? a.added : history[0].date,
  };
}

export function lastChange(a) {
  return a.history[a.history.length - 1]?.date ?? a.added;
}

export function isOverdue(a, day = today()) {
  return Boolean(a.followUp) && OPEN.has(a.status) && a.followUp <= day;
}

// Overdue follow-ups first, then the most recent change, then the newest added.
export function sorted(items, day = today()) {
  return [...items].sort((x, y) =>
    isOverdue(y, day) - isOverdue(x, day) ||
    lastChange(y).localeCompare(lastChange(x)) ||
    y.added.localeCompare(x.added) || x.title.localeCompare(y.title));
}

export function counts(items, day = today()) {
  const out = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const a of items) out[a.status]++;
  out.all = items.length;
  out.overdue = items.filter((a) => isOverdue(a, day)).length;
  return out;
}

// The list, with every change saved right away. `storage` is localStorage or a stand-in;
// when it can't be used (private mode, blocked), the list lives in memory and `persistent` is false.
export function createStore(storage, { onChange = () => {} } = {}) {
  let items = [];
  let persistent = true;
  try {
    const raw = storage?.getItem(KEY);
    const data = raw ? JSON.parse(raw) : null;
    items = (Array.isArray(data?.items) ? data.items : []).map((a) => clean(a)).filter(Boolean);
    storage.setItem(`${KEY}.probe`, "1");
    storage.removeItem(`${KEY}.probe`);
  } catch {
    persistent = false;
  }

  function save() {
    if (persistent) {
      try {
        storage.setItem(KEY, JSON.stringify({ version: VERSION, items }));
      } catch {
        persistent = false;
      }
    }
    onChange();
  }

  return {
    get persistent() { return persistent; },
    all: () => items.slice(),
    get: (id) => items.find((a) => a.id === id) ?? null,
    findByRef: (ref) => (ref ? items.find((a) => a.ref === ref) ?? null : null),

    add(input, day = today()) {
      const a = clean({ ...input, status: input.status ?? "saved", history: [], added: day }, day);
      if (!a) return null;
      if (a.ref && items.some((x) => x.ref === a.ref)) return items.find((x) => x.ref === a.ref);
      items.push(a);
      save();
      return a;
    },

    setStatus(id, status, day = today()) {
      const a = items.find((x) => x.id === id);
      if (!a || !STATUSES.includes(status) || a.status === status) return;
      a.status = status;
      a.history.push({ status, date: day });
      if (!OPEN.has(status)) a.followUp = null;    // offer or rejection: nothing to follow up
      save();
    },

    update(id, fields) {
      const a = items.find((x) => x.id === id);
      if (!a) return;
      if ("note" in fields) a.note = text(fields.note, MAX_TEXT.note);
      if ("followUp" in fields) a.followUp = isDay(fields.followUp) ? fields.followUp : null;
      for (const k of ["title", "company", "city"]) {
        if (k in fields && text(fields[k], MAX_TEXT[k])) a[k] = text(fields[k], MAX_TEXT[k]);
      }
      if ("url" in fields) a.url = safeUrl(fields.url);
      save();
    },

    remove(id) {
      const before = items.length;
      items = items.filter((a) => a.id !== id);
      if (items.length !== before) save();
    },

    // A file for backup or another device.
    exportJson() {
      return JSON.stringify({ app: "werkstudent-radar", version: VERSION, exported: today(), items }, null, 1);
    },

    // Merge an export file: new applications are added; one that exists already keeps
    // whichever copy changed last. Returns { added, updated } or throws on a bad file.
    importJson(raw) {
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        throw new Error("not a JSON file");
      }
      if (!Array.isArray(data?.items)) throw new Error("no applications in this file");
      let added = 0;
      let updated = 0;
      for (const a of data.items.map((x) => clean(x)).filter(Boolean)) {
        const i = items.findIndex((x) => x.id === a.id);
        if (i < 0) {
          items.push(a);
          added++;
        } else if (lastChange(a) > lastChange(items[i])) {
          items[i] = a;
          updated++;
        }
      }
      if (added || updated) save();
      return { added, updated };
    },
  };
}
