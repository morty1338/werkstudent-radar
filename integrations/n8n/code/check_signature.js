// "Check signature": accept only calls from the Werkstudent Radar pipeline.
// radar/notify.py sends X-Radar-Signature: sha256=HMAC-SHA256(secret, "<timestamp>." + body)
// and X-Radar-Timestamp; this node recomputes it over the raw body bytes. It also drops the
// postings already sent that day, so a repeated daily run posts only the ones it found later
// and a day with nothing left is skipped; test payloads always pass.
//
// The secret: self-hosted, WEBHOOK_SECRET in the environment (.env next to docker-compose.yml).
// n8n Cloud has no environment variables: paste it into SECRET below instead.
const SECRET = '';
const MAX_AGE_S = 600;   // a call older than 10 minutes is a replay

const crypto = require('crypto');
let secret = SECRET;
if (!secret) {
  try { secret = $env.WEBHOOK_SECRET || ''; } catch (e) { secret = ''; }
}
const reject = (status, reason) => [{ json: { ok: false, status, reason } }];
if (!secret) return reject(500, 'WEBHOOK_SECRET is not set in n8n');

const item = $input.first();
const headers = item.json.headers || {};
const ts = String(headers['x-radar-timestamp'] || '');
const given = String(headers['x-radar-signature'] || '');
if (!/^\d{1,12}$/.test(ts) || Math.abs(Date.now() / 1000 - Number(ts)) > MAX_AGE_S) {
  return reject(401, 'missing or expired timestamp');
}

// The exact bytes that were signed (Webhook node option "Raw Body"); parsed JSON would differ.
const raw = await this.helpers.getBinaryDataBuffer(0, 'data');
const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(ts + '.').update(raw).digest('hex');
const a = Buffer.from(given);
const b = Buffer.from(expected);
if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return reject(401, 'bad signature');

let payload;
try {
  payload = JSON.parse(raw.toString('utf8'));
} catch (e) {
  return reject(400, 'body is not JSON');
}
if (payload.event !== 'new_postings' || !Array.isArray(payload.jobs)) return reject(400, 'unknown event');

// "Build messages" stores the day it sent last and the ids of that day's postings.
const sent = $getWorkflowStaticData('global');
if (!payload.test && sent.lastAsOf === payload.as_of) {
  // Stored before the ids were (only lastAsOf): the whole day counts as sent.
  const ids = new Set(Array.isArray(sent.sentIds) ? sent.sentIds : payload.jobs.map((j) => j.id));
  const jobs = payload.jobs.filter((j) => !ids.has(j.id));
  if (!jobs.length) return [{ json: { ok: false, status: 200, reason: `${payload.as_of} was already sent` } }];
  const total = payload.total - (payload.jobs.length - jobs.length);
  return [{ json: { ok: true, status: 200, payload: { ...payload, jobs, total, more: true } } }];
}
return [{ json: { ok: true, status: 200, payload } }];
