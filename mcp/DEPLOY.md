# Hosting the remote MCP server

The server (`radar_server.py --http`, see `radar_http.py`) is a small stateless web
service: no database, no disk, no login, data fetched from GitHub Pages and kept in
memory for an hour. It needs a host that runs Python and gives it an HTTPS URL,
**for free and without a payment method**, so that it can never cost money.

## Hosts compared (October 2026)

| | Vercel (Hobby) | Render (Free) | Google Cloud Run |
|---|---|---|---|
| Price | free | free | free tier: 2 million requests, 180,000 vCPU-s, 360,000 GiB-s a month |
| Payment method | **none** | **none** | billing account with a card, even within the free tier |
| Above the limits | the feature pauses until the 30 days are over; nothing is billed | free services are suspended for the month | billed |
| Monthly limits | 1,000,000 function invocations, 4 hours of active CPU, 360 GB-hours of memory, 100 GB transfer | 750 instance hours per workspace, 5 GB bandwidth | as above |
| When idle | scales to zero | spins down after 15 minutes | scales to zero |
| Cold start | about a second or two (Python function) | **about a minute** | a few seconds |
| Runs | Python ASGI apps as functions (`app` in `app.py`), lifespan events supported | Docker images | Docker images |
| Terms | personal, non-commercial use | — | — |

Also looked at: Hugging Face Spaces (Docker Spaces now need a paid PRO plan), Fly.io
(no free tier for new organizations), Koyeb (its pages disagree on whether a card is
needed).

Sources: [Vercel Hobby plan](https://vercel.com/docs/plans/hobby),
[Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python),
[Render free instances](https://render.com/docs/free), [Render pricing](https://render.com/pricing),
[Cloud Run free tier](https://docs.cloud.google.com/free/docs/free-cloud-features),
[Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview),
[Fly.io pricing](https://docs.fly.io/about/pricing).

**Choice: Vercel Hobby.** It is the only one of the three that is free, needs no
payment method and can't bill anything: past a limit the project pauses. It wakes in
about a second, where Render's free tier takes about a minute, long enough for Claude
to give up on a tool call. The limits are far above this server's use (a question
causes one to three tool calls of a few milliseconds of CPU each; the one-off data
download after a cold start is under a second). A portfolio project is personal,
non-commercial use. Vercel runs the server as a function: each instance keeps its
own copy of the data and its own rate limits, and Vercel's DDoS protection sits in
front.

If Vercel ever stops fitting, `mcp/Dockerfile` runs the same server on any container
host, including Render's free tier (with its one-minute wake-up).

## How the deploy works

`.github/workflows/mcp-deploy.yml` runs when `mcp/` changes on main (or by hand):

1. **test**: the MCP tests, then the Docker image is built, started and asked for
   `get_overview` over HTTP;
2. **deploy**: `mcp/vercel/stage.sh` puts only the server into `build/` (the entrypoint
   `mcp/vercel/app.py` as `app.py`, `mcp/radar_*.py`, `radar/stats.py`, the SDK as the
   only requirement, Python 3.12), and the Vercel CLI deploys that folder as the
   production deployment of the project `radar-mcp`, then checks `/health`.

The website and the data pipeline aren't part of this deployment; the server reads
the published JSON like any visitor.

## One-time setup (the owner)

No payment details anywhere. The only secret is a Vercel token, stored in GitHub's
encrypted secrets and never in the repository.

1. **Vercel account**: sign up at <https://vercel.com/signup>, choose **Hobby**
   ("I'm working on personal projects"), and sign in with GitHub or e-mail. When
   asked to import a Git repository, skip it: connecting the repository would make
   Vercel deploy the whole repo on every commit, including the bot's daily data
   commits. The workflow deploys only the server, only when it changes.

2. **Token**: avatar → Account Settings → Tokens (<https://vercel.com/account/tokens>)
   → Create. Name `github-radar-mcp`, scope: your Hobby team, expiration: 1 year
   (put a reminder in your calendar to renew it). Copy the token; it is shown once.

3. **GitHub**: repository → Settings → Secrets and variables → Actions → **Secrets** →
   New repository secret: name `VERCEL_TOKEN`, value: the token.

4. **First deploy**: Actions → "Deploy MCP server" → Run workflow. The first run
   creates the project `radar-mcp`; the run summary shows the address, normally
   `https://radar-mcp.vercel.app/mcp` (Vercel adds a suffix if the name is taken).
   Later pushes that change `mcp/` deploy by themselves.

To take the server offline: Vercel dashboard → radar-mcp → Settings → delete the
project (or pause it). To stop deploys: delete the `VERCEL_TOKEN` secret.
