// Kurs-Radar numbers for the page (kurs.js), without the DOM: shares with enough postings,
// week-over-week change, must/nice split and the "Opportunities" score. No imports, so
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

// Each value scaled to 0…1 between the smallest and the largest (all equal: 0.5).
function scaled(values) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return values.map((v) => (hi > lo ? (v - lo) / (hi - lo) : 0.5));
}

// Opportunities: job titles with enough postings, scored 0–100 by the mean of three parts,
// each scaled 0–100 among the rated titles:
//   volume     postings on the board, on a log scale (2 vs. 20 postings counts like 200 vs. 2,000)
//   momentum   share of postings new in the last 7 days
//   openness   share open to career changers
// Returns { rows (with .parts = { volume, momentum, openness }), skipped }.
export function opportunities(data) {
  const all = data.academies.flatMap((a) => a.titles.map((x) => ({ ...x, academy: a })));
  const rated = all.filter((x) => x.sample >= MIN_BASE && shareOf(x.quereinsteiger) != null);
  const skipped = all.filter((x) => !rated.includes(x));
  const vol = scaled(rated.map((x) => Math.log10(Math.max(x.postings, 1))));
  const mom = scaled(rated.map((x) => x.new_7d.share ?? 0));
  const open = scaled(rated.map((x) => x.quereinsteiger.share));
  const rows = rated
    .map((x, i) => ({
      ...x,
      momentum: x.new_7d.share ?? 0,
      parts: { volume: Math.round(vol[i] * 100), momentum: Math.round(mom[i] * 100), openness: Math.round(open[i] * 100) },
      score: Math.round(((vol[i] + mom[i] + open[i]) / 3) * 100),
    }))
    .sort((a, b) => b.score - a.score || b.postings - a.postings);
  return { rows, skipped };
}

// Share of postings asking for each skill, every group (job title) with at least MIN_BASE
// postings weighing the same; null when fewer than two groups are that big (then the plain
// share is right). groups: arrays of skill-index lists, one list per posting; n: number of skills.
// The page's academy view in Jobs mode; kurs_stats.equal_weight does the same for kurs.json.
export function equalShares(groups, n) {
  const big = groups.filter((g) => g.length >= MIN_BASE);
  if (big.length < 2) return null;
  const out = new Array(n).fill(0);
  for (const g of big) {
    const counts = new Array(n).fill(0);
    for (const skills of g) for (const s of skills) counts[s]++;
    counts.forEach((c, s) => (out[s] += c / g.length / big.length));
  }
  return out;
}

