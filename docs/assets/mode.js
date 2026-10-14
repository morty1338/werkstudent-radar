// Which data set the page shows, read from the URL like the language: "werkstudent" (default)
// or "jobs" (?mode=jobs: all working times for the Kurs-Radar job titles, docs/data/jobs_*.json).
// JOBS_PUBLIC switches Jobs mode on for everyone: the switch shows, ?mode=jobs works and kurs.html
// redirects here. With false it needs ?beta=1 (the beta while it was built). No imports (tests run it in Node).

export const JOBS_PUBLIC = true;

const params = new URLSearchParams(globalThis.location?.search ?? "");
export const beta = params.get("beta") === "1";
export const jobsAllowed = JOBS_PUBLIC || beta;
export const mode = jobsAllowed && params.get("mode") === "jobs" ? "jobs" : "werkstudent";

// Files with a Jobs-mode twin (jobs_<name>.json); patterns.json is the same for both.
const JOBS_FILES = new Set(["summary", "checker", "postings", "history", "lifetimes", "cooccurrence", "companies"]);
export const dataUrl = (name) => `data/${mode === "jobs" && JOBS_FILES.has(name) ? "jobs_" : ""}${name}.json`;

// Tracker keys of the postings on the page: the Werkstudent page checks "p:"/"c:" against its
// data, Jobs mode (and the old kurs.html) uses "k:" and "kc:", so neither marks the other's as gone.
export const refPrefix = mode === "jobs" ? { posting: "k:", company: "kc:" } : { posting: "p:", company: "c:" };

// The page in the other mode: the city, "no German", skills, language and beta carry over;
// field and academy belong to one data set and are dropped.
export function modeUrl(target, href = globalThis.location.href) {
  const url = new URL(href);
  for (const k of ["field", "academy", "co"]) url.searchParams.delete(k);
  if (target === "jobs") url.searchParams.set("mode", "jobs");
  else url.searchParams.delete("mode");
  url.hash = "";
  return url.href;
}
