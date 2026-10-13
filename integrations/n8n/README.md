# Telegram alerts with n8n

Every morning the daily pipeline sends today's new Werkstudent postings to an n8n
workflow, which filters them and posts short messages to Telegram:

```
GitHub Actions (daily.yml)            n8n                                     Telegram
radar/notify.py  ──POST + signature──▶ Webhook → Check signature → Valid?
                                                     │ no → Reject (401)
                                                     │ yes → Accept (200)
                                                     ▼
                                       Build messages (filter per target, ≤10 jobs)
                                                     ├──▶ Telegram: public channel ──▶ @your_channel
                                                     └──▶ Telegram: my chat        ──▶ you
```

| File | What it is |
|---|---|
| `werkstudent-radar-alerts.json` | the workflow to import into n8n (built, don't edit by hand) |
| `code/check_signature.js`, `code/build_messages.js` | the two Code nodes; `build_workflow.py` puts them into the JSON |
| `docker-compose.yml`, `ngrok-policy.yml`, `.env.example` | n8n on your own computer, reachable through ngrok |

The payload, the signature and the "new postings" rules are described in
`radar/notify.py`. No secret is stored in this folder: the webhook secret lives in
`.env` (git-ignored) and GitHub Secrets, the bot token in an n8n credential.

## Where to run n8n

GitHub Actions has to reach n8n over HTTPS when the daily run finishes (usually
06:30–07:30 Berlin time).

| | n8n Cloud (trial) | Self-hosted with Docker (+ ngrok) |
|---|---|---|
| Cost | 14 days free, no card; then from about €20/month, or the workspace is deleted | free (n8n Community Edition; ngrok free account, no card) |
| Setup | sign up, import, done (~10 min) | install Docker Desktop, fill `.env`, `docker compose up -d` (~30 min) |
| Always online | yes | only while your computer is on and awake |
| Limits | 1,000 executions in the trial (one per day here) | none from n8n; ngrok free: 20,000 requests/month |
| Webhook secret | pasted into the "Check signature" node (Cloud has no environment variables) | in `.env`, never in the workflow |
| Data | on n8n's servers | on your computer (Docker volume) |

**Recommendation: self-host with Docker.** It costs nothing after 14 days, your
data stays with you, and it's the better story in an interview (you run the whole
chain yourself). The catch is that the computer has to be awake in the morning; if it
wasn't, the run shows a warning and you can send the day again by hand (step 6, mode resend). Use
the Cloud trial only if you want to try n8n before installing Docker.

## Setup

### 1. Create the bot (@BotFather)

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Give it a name ("Werkstudent Radar") and a username ending in `bot`
   (e.g. `werkstudent_radar_bot`).
3. BotFather answers with a **token** like `123456789:AA…`. It's a password for the
   bot: never put it in the repository, a chat or a screenshot. You'll paste it into
   n8n in step 4.

### 2. The public channel and your chat ID

**Channel (all new Berlin jobs):**
1. Telegram → New Channel → name it, make it **Public**, pick a link such as
   `t.me/werkstudent_jobs_berlin`. The chat ID is then `@werkstudent_jobs_berlin`.
2. Channel → Administrators → Add Admin → your bot, with **Post messages** allowed.

**Your personal chat:**
1. Open your bot and press **Start** (a bot can only write to people who started it).
2. Send it any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in your browser (with your token).
3. Find `"chat":{"id":123456789,…}`: that number is your chat ID.

### 3a. Run n8n with Docker (recommended)

1. Install **Docker Desktop** (free for personal use and education) and start it.
2. Create a free account on **ngrok.com** (no card). In the dashboard, copy your
   **Authtoken** and, under Domains, your free **dev domain**
   (`something.ngrok-free.app`).
3. In this folder:
   ```bash
   cp .env.example .env
   ```
   Fill in `WEBHOOK_SECRET` (make one with `openssl rand -hex 32` and keep it for
   GitHub in step 5), `NGROK_AUTHTOKEN` and `NGROK_DOMAIN`.
4. Start it:
   ```bash
   docker compose up -d
   ```
5. Open http://localhost:5678 and create the owner account (local to your computer).
   Only `https://<NGROK_DOMAIN>/webhook/…` is public; ngrok answers 404 for
   everything else (`ngrok-policy.yml`), so the editor isn't on the internet.

To keep it running in the morning: Docker Desktop → Settings → "Start Docker Desktop
when you sign in", and on a Mac System Settings → Battery → Options → "Prevent
automatic sleeping on power adapter when the display is off" (plugged in overnight).

### 3b. Or: n8n Cloud trial

Sign up at n8n.io (no card). Your webhook address will be
`https://<name>.app.n8n.cloud/webhook/werkstudent-radar`. After importing (step 4),
open **Check signature** and paste the webhook secret between the quotes of
`const SECRET = '';`. Don't export or share the workflow afterwards: it then
contains the secret.

### 4. Import the workflow and set it up

1. n8n → **Create workflow** → ⋯ menu → **Import from File** →
   `werkstudent-radar-alerts.json`.
2. **Credentials:** open **Telegram: public channel** → Credential → *Create new* →
   *Telegram API* → paste the bot token → save as "Werkstudent Radar bot". Select the
   same credential in **Telegram: my chat**.
3. Open **Build messages** and edit `TARGETS` at the top:
   - `channel`: `chatId: '@werkstudent_jobs_berlin'` (your channel), `cities: ['Berlin']`.
   - `me`: `chatId: '123456789'` (your chat ID) and your own filter: `cities`,
     `fields` (ids such as `it`, `data`), `skills` (names as on the site, e.g.
     `'Python'`), `noGerman: true` for jobs without German, `includeRemote`.
4. **Save**, then **Publish**. The production URL is
   `https://<NGROK_DOMAIN>/webhook/werkstudent-radar` (Webhook node → Production URL).

### 5. Put the URL into GitHub Secrets

github.com/morty1338/werkstudent-radar → Settings → Secrets and variables → Actions →
**New repository secret**, twice:

| Name | Value |
|---|---|
| `N8N_WEBHOOK_URL` | the production URL from step 4, e.g. `https://something.ngrok-free.app/webhook/werkstudent-radar` |
| `WEBHOOK_SECRET` | exactly the same string as in `.env` (or in the Cloud node) |

From the next daily run on, the step "Send Telegram alerts" calls the webhook.

### 6. Try it

github.com/morty1338/werkstudent-radar → **Actions** → **Telegram alerts** → **Run
workflow** → mode **test** → Run. It sends the latest day's new postings marked as a
test: a "🧪 Test" message arrives in both chats, and the day still counts as unsent.
The run fails (red) when the secrets are missing or n8n doesn't answer, with the reason
in the run's summary.

Mode **resend** sends the latest day for real: use it when your computer was off
during the daily run (the daily step then shows a warning). n8n skips a day it already
posted and says so in the log.

From your own computer, with the secret from `.env`:

```bash
read -rs WEBHOOK_SECRET && export WEBHOOK_SECRET && N8N_WEBHOOK_URL=https://<NGROK_DOMAIN>/webhook/werkstudent-radar .venv/bin/python -m radar.notify --test --strict
```

Every run shows up in n8n under **Executions**.

## Good to know

- **Answers:** 200 = accepted, or the day was already sent (`reason` says so);
  401 = wrong secret, changed body or a timestamp older than 10 minutes;
  500 = `WEBHOOK_SECRET` is missing in n8n. A 404 means the workflow isn't published
  or the path is wrong.
- **Telegram errors** (n8n → Executions → the red node): "Not Found" means a wrong bot
  token in the credential (paste it again, without a `bot` prefix or spaces);
  "Credential with ID … does not exist" means the credential was deleted and made again.
  Give the new one another name, select it in both Telegram nodes, Save and Publish
  (with the same name n8n sees no change and keeps Publish greyed out).
- **A day is posted once.** "Build messages" remembers the last day it sent; a
  repeated daily run is answered with "already sent". Test payloads always go through.
- **Changing the code:** edit `code/*.js`, run `python integrations/n8n/build_workflow.py`
  (a test fails if the JSON is out of date), and import again. Re-importing into the
  same workflow only creates a draft: delete the old workflow first, or publish the new
  version, and set the credentials and `TARGETS` again.
- **Tested** with n8n 2.43.4 (self-hosted): signed calls, wrong secret, changed body,
  replay, a repeated day, a test payload and an empty day (Telegram replaced by a
  stand-in); n8n Cloud was not tried. n8n's Code node has no `URL`/`URLSearchParams`;
  `tests/test_n8n.py` runs both scripts without them.
