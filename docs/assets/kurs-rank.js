// Kurs-Radar numbers for the page (kurs.js), without the DOM: shares with enough postings,
// week-over-week change, must/nice split and the "Opportunities" ranking. No imports, so
// tests run it in Node; mcp/radar_tools.py kurs_opportunities ranks the same way.

export const MIN_BASE = 20;        // fewer postings behind a share: show "—" instead of a percentage
export const MIN_IMPORTANCE = 5;   // fewer postings read by the LLM for a skill: no must/nice split

// A share with its base: null when the base is too small to mean anything.
export const shareOf = (m) => (m && m.base >= MIN_BASE ? m.share : null);

// Week-over-week change as a fraction, or null.
export const wowChange = (w) => (w && w.before ? (w.now - w.before) / w.before : null);

// Must-have part of a skill's bar, or null when too few postings were read by the LLM.
export function mustFraction(s) {
  const n = s.must + s.nice;
  return n >= MIN_IMPORTANCE ? s.must / n : null;
}

// Mean of the percentile ranks (0 = lowest, 1 = highest; ties share a rank) of each value.
function ranks(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return values.map((v) => {
    if (values.length < 2) return 0.5;
    const first = sorted.indexOf(v);
    const last = sorted.lastIndexOf(v);
    return (first + last) / 2 / (values.length - 1);
  });
}

// Opportunities: job titles with enough postings, scored by the average rank of size,
// momentum (week-over-week change once every title has one, else the share published in
// the last 7 days) and the Quereinsteiger share. Returns { rows, skipped, momentum }.
export function opportunities(data) {
  const all = data.academies.flatMap((a) => a.titles.map((x) => ({ ...x, academy: a })));
  const rated = all.filter((x) => x.sample >= MIN_BASE && shareOf(x.quereinsteiger) != null);
  const skipped = all.filter((x) => !rated.includes(x));
  const useWow = rated.length > 0 && rated.every((x) => wowChange(x.wow) != null);
  const momentum = (x) => (useWow ? wowChange(x.wow) : x.new_7d.share ?? 0);
  const r1 = ranks(rated.map((x) => x.postings));
  const r2 = ranks(rated.map(momentum));
  const r3 = ranks(rated.map((x) => x.quereinsteiger.share));
  const rows = rated
    .map((x, i) => ({ ...x, momentum: momentum(x), score: Math.round(((r1[i] + r2[i] + r3[i]) / 3) * 100) }))
    .sort((a, b) => b.score - a.score || b.postings - a.postings);
  return { rows, skipped, momentum: useWow ? "wow" : "new_7d" };
}
