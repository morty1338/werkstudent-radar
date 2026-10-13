// "Build messages": one Telegram message per target with the new jobs that pass its filter,
// then marks the day as sent ("Check signature" skips it if the daily run is repeated).
// Edit the targets here after importing. Empty lists mean "no filter".
//   chatId         "@channel_name" for a public channel, or the numeric chat ID (see README)
//   cities         as on the site: "Berlin", "München", "Hamburg", …
//   includeRemote  also jobs that are remote anywhere in Germany (no city)
//   fields         field ids: it, data, engineering, marketing, sales, finance, consulting,
//                  product, design, hr, ops, research, admin, legal, education, health, retail, other
//   skills         skill names as on the site ("Python", "SQL", "Excel", …); a job needs one of them
//   noGerman       only jobs that don't require German
const TARGETS = [
  { target: 'channel', chatId: '@your_channel', cities: ['Berlin'], includeRemote: false, fields: [], skills: [], noGerman: false },
  { target: 'me', chatId: '123456789', cities: ['Berlin'], includeRemote: true, fields: ['it', 'data'], skills: [], noGerman: false },
];
const MAX_JOBS = 10;     // per message; the rest is "+N more" with a link to the site

const p = $input.first().json.payload;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
const lower = (list) => (list || []).map((s) => String(s).toLowerCase());
const [y, m, d] = String(p.as_of).split('-').map(Number);
const day = `${d} ${MONTHS[m - 1]} ${y}`;

function matches(job, t) {
  const cities = t.cities || [];
  if (cities.length && !(cities.includes(job.city) || (t.includeRemote && job.city === ''))) return false;
  if ((t.fields || []).length && !t.fields.includes(job.field)) return false;
  const skills = lower(t.skills);
  if (skills.length && !lower(job.skills).some((s) => skills.includes(s))) return false;
  if (t.noGerman && !job.no_german) return false;
  return true;
}

// The site with the target's filter, where the rest of the jobs can be found.
// (No URLSearchParams: n8n's Code node sandbox doesn't have it.)
function siteLink(t) {
  const q = [];
  if ((t.fields || []).length === 1) q.push(['field', t.fields[0]]);
  if ((t.cities || []).length === 1) q.push(['city', t.cities[0]]);
  if (t.noGerman) q.push(['german', 'no']);
  const s = q.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  return p.site + (s ? `?${s}` : '');
}

function line(job) {
  const pay = typeof job.pay === 'number' ? `€${job.pay.toFixed(2)}/h` : null;
  const where = job.city || 'Remote (DE)';
  const facts = [cut(job.company || '', 60), where, pay, job.field_label].filter(Boolean).map(esc).join(' · ');
  return `• <a href="${esc(job.url)}">${esc(cut(job.title || '', 120))}</a>\n   ${facts}`;
}

const out = [];
for (const t of TARGETS) {
  const jobs = p.jobs.filter((j) => matches(j, t))
    .sort((a, b) => (b.pay ?? -1) - (a.pay ?? -1) || String(a.title).localeCompare(String(b.title)));
  if (!jobs.length) continue;
  const where = (t.cities || []).length ? t.cities.join(', ') : 'Germany';
  const head = `${p.test ? '🧪 Test · ' : ''}<b>${jobs.length} new Werkstudent job${jobs.length === 1 ? '' : 's'} · ${esc(where)}</b>\n${day}`;
  const shown = jobs.slice(0, MAX_JOBS).map(line);
  const more = jobs.length - shown.length;
  const tail = more > 0
    ? `<a href="${esc(siteLink(t))}">+${more} more on Werkstudent Radar</a>`
    : `<a href="${esc(siteLink(t))}">Werkstudent Radar</a>`;
  out.push({ json: { target: t.target, chatId: t.chatId, count: jobs.length, text: [head, shown.join('\n'), tail].join('\n\n') } });
}
// Remember the day only now, so a day whose messages couldn't be built isn't skipped later.
if (!p.test) $getWorkflowStaticData('global').lastAsOf = p.as_of;
return out;
