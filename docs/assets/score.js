// Match score of one posting against your skills, with the reasons behind it.
// No imports and no DOM, so it can be tested in Node.
//
// Required skills count 80 %, nice-to-have skills 20 %: the score is
// 0.8 × (share of required skills you have) + 0.2 × (share of nice-to-have
// skills you have). Within each share a basic skill (MS Office, Excel, …: asked
// for nearly everywhere) weighs BASIC_WEIGHT of a specific skill, so the basics
// alone can't lift a posting near 100 %; a kind made of basic skills only gets
// BASIC_WEIGHT of its 80 or 20 % (MS Office required, Python a plus: the basics
// don't make 80 %). A posting without one of the two kinds
// gives the whole weight to the other kind, so a posting that lists only
// nice-to-have skills is scored by those alone, and one without nice-to-have
// skills (every posting whose skills were found by the rules, not the LLM) by
// its required skills. Postings with fewer than MIN_SKILLS skills, or with
// basic skills only, get no score but the flag `few`: one matching skill, or
// MS Office and Excel, would read as 100 %.

export const MUST_WEIGHT = 0.8;
export const NICE_WEIGHT = 0.2;
export const BASIC_WEIGHT = 0.25;
export const MIN_SKILLS = 2;

// skills: skill indices of the posting; nice: the ones marked nice to have
// (a subset of skills); have: Set of your skill indices; basic: Set of the
// indices of basic skills.
// Returns { score: 0..1 or null, few, have, missingMust, missingNice } with the
// skill indices in the posting's order.
export function scoreJob(skills, nice, have, basic = new Set()) {
  const isNice = new Set(nice || []);
  const out = { score: null, few: skills.length < MIN_SKILLS || skills.every((s) => basic.has(s)),
    have: [], missingMust: [], missingNice: [] };
  // Weighted totals, and how many skills of each kind are specific (not basic).
  let must = 0, mustHave = 0, plus = 0, plusHave = 0, mustSpecific = 0, plusSpecific = 0;
  for (const s of skills) {
    const owned = have.has(s);
    const w = basic.has(s) ? BASIC_WEIGHT : 1;
    if (owned) out.have.push(s);
    if (isNice.has(s)) {
      plus += w;
      plusSpecific += w === 1;
      if (owned) plusHave += w;
      else out.missingNice.push(s);
    } else {
      must += w;
      mustSpecific += w === 1;
      if (owned) mustHave += w;
      else out.missingMust.push(s);
    }
  }
  if (out.few) return out;
  const wMust = must ? MUST_WEIGHT * (mustSpecific ? 1 : BASIC_WEIGHT) : 0;
  const wNice = plus ? NICE_WEIGHT * (plusSpecific ? 1 : BASIC_WEIGHT) : 0;
  out.score = ((must ? wMust * mustHave / must : 0) + (plus ? wNice * plusHave / plus : 0)) / (wMust + wNice);
  return out;
}
