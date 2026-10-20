// Match score of one posting against your skills, with the reasons behind it.
// No imports and no DOM, so it can be tested in Node.
//
// Required skills count 80 %, nice-to-have skills 20 %: the score is
// 0.8 × (share of required skills you have) + 0.2 × (share of nice-to-have
// skills you have). A posting without one of the two kinds gives the whole
// weight to the other kind, so a posting that lists only nice-to-have skills
// is scored by those alone, and one without nice-to-have skills (every posting
// whose skills were found by the rules, not the LLM) by its required skills.
// Postings with fewer than MIN_SKILLS skills, none at all included, get no
// score but the flag `few`: one matching skill would read as 100 %.

export const MUST_WEIGHT = 0.8;
export const NICE_WEIGHT = 0.2;
export const MIN_SKILLS = 2;

// skills: skill indices of the posting; nice: the ones marked nice to have
// (a subset of skills); have: Set of your skill indices.
// Returns { score: 0..1 or null, few, have, missingMust, missingNice } with the
// skill indices in the posting's order.
export function scoreJob(skills, nice, have) {
  const isNice = new Set(nice || []);
  const out = { score: null, few: skills.length < MIN_SKILLS, have: [], missingMust: [], missingNice: [] };
  let must = 0, mustHave = 0, plus = 0, plusHave = 0;
  for (const s of skills) {
    const owned = have.has(s);
    if (owned) out.have.push(s);
    if (isNice.has(s)) {
      plus++;
      if (owned) plusHave++;
      else out.missingNice.push(s);
    } else {
      must++;
      if (owned) mustHave++;
      else out.missingMust.push(s);
    }
  }
  if (out.few) return out;
  const wMust = must ? MUST_WEIGHT : 0;
  const wNice = plus ? NICE_WEIGHT : 0;
  out.score = ((must ? wMust * mustHave / must : 0) + (plus ? wNice * plusHave / plus : 0)) / (wMust + wNice);
  return out;
}
