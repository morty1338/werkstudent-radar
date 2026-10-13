// "Check signature": accept only calls from the Werkstudent Radar pipeline.
// radar/notify.py sends X-Radar-Signature: sha256=HMAC-SHA256(secret, "<timestamp>." + body)
// and X-Radar-Timestamp; this node recomputes it over the raw body bytes. It also skips a
// day that was already sent (a re-run of the daily workflow); test payloads always pass.
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

// "Build messages" stores the day it sent last.
if (!payload.test && $getWorkflowStaticData('global').lastAsOf === payload.as_of) {
  return [{ json: { ok: false, status: 200, reason: `${payload.as_of} was already sent` } }];
}
return [{ json: { ok: true, status: 200, payload } }];
