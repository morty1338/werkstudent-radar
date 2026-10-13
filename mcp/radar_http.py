"""Remote mode of the MCP server: the same tools over Streamable HTTP.

    mcp/.venv/bin/python mcp/radar_server.py --http --port 8000

Clients (Claude custom connectors, Claude Code, the MCP Inspector) connect to
`https://<host>/mcp`. The server is public and read-only, so there is no login;
instead:

- stateless JSON responses: every request stands alone (no sessions to keep, any
  instance can answer, and a restart loses nothing);
- a request body limit (64 KiB; tool calls are a few hundred bytes);
- rate limits: per client address (a token bucket) and for the whole server, so
  one client or a burst can't use up a small free hosting tier;
- `/health` for the host's health checks, `/` with a short description;
- one log line per request with method, path, status and duration, and the tool
  name for tool calls; no IP addresses, user agents or arguments are logged.
  Client addresses are only kept in memory for rate limiting.

Behind a host's proxy that doesn't hand uvicorn the client address (Vercel),
`trust_proxy=True` takes it from X-Real-IP / X-Forwarded-For, which that proxy
sets itself. Allowed Host headers come from MCP_ALLOWED_HOSTS (comma-separated, e.g.
"radar-mcp.example.com"); "*" turns the DNS-rebinding check off, which is what
you want behind a hosting proxy that sets the Host header itself.
"""

import json
import logging
import os
import time

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_BODY = 64 * 1024
PER_CLIENT_RATE = 1.0        # tokens per second per client address (60 a minute)
PER_CLIENT_BURST = 30        # bucket size: a short burst of tool calls is fine
GLOBAL_PER_MINUTE = 600      # all clients together
LOCAL_HOSTS = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"]

log = logging.getLogger("radar_http")


def transport_security(env=None):
    """Host header check from MCP_ALLOWED_HOSTS; local names only when it's unset."""
    raw = (env if env is not None else os.environ.get("MCP_ALLOWED_HOSTS", "")).strip()
    if raw == "*":
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    allowed = LOCAL_HOSTS + [x for h in hosts for x in (h, f"{h}:*")]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed, allowed_origins=[])


class RateLimiter:
    """Token bucket per client address plus a fixed one-minute window for everyone."""

    def __init__(self, rate=PER_CLIENT_RATE, burst=PER_CLIENT_BURST, global_per_minute=GLOBAL_PER_MINUTE, clock=time.monotonic):
        self.rate, self.burst, self.global_per_minute, self.clock = rate, burst, global_per_minute, clock
        self.buckets = {}            # address -> (tokens, last time)
        self.window = (0.0, 0)       # (start, requests in it)

    def allow(self, client):
        """(allowed, seconds to wait)."""
        now = self.clock()
        start, count = self.window
        if now - start >= 60:
            start, count = now, 0
            # forget clients idle for more than 10 minutes
            self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < 600}
        if count >= self.global_per_minute:
            return False, max(1, int(60 - (now - start)) + 1)
        tokens, last = self.buckets.get(client, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self.buckets[client] = (tokens, now)
            return False, max(1, int((1 - tokens) / self.rate) + 1)
        self.buckets[client] = (tokens - 1, now)
        self.window = (start, count + 1)
        return True, 0


class Guard:
    """ASGI middleware around the MCP app: rate limit and body limit for /mcp, request log."""

    def __init__(self, app, limiter=None, max_body=MAX_BODY, trust_proxy=False):
        self.app, self.limiter, self.max_body, self.trust_proxy = app, limiter or RateLimiter(), max_body, trust_proxy

    def client(self, scope):
        """The client address for rate limiting (kept in memory only, never logged)."""
        if self.trust_proxy:
            headers = dict(scope.get("headers") or [])
            ip = headers.get(b"x-real-ip") or (headers.get(b"x-forwarded-for") or b"").split(b",")[0]
            if ip.strip():
                return ip.strip().decode("latin-1")
        return (scope.get("client") or ("unknown", 0))[0]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started, path = time.monotonic(), scope["path"]
        status = {"code": 500, "tool": None}

        async def send_logged(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            if path.startswith("/mcp"):
                ok, wait = self.limiter.allow(self.client(scope))
                if not ok:
                    resp = JSONResponse({"error": "Too many requests; try again shortly."}, status_code=429,
                                        headers={"Retry-After": str(wait)})
                    return await resp(scope, receive, send_logged)
                length = dict(scope.get("headers") or []).get(b"content-length")
                if length is not None and (not length.isdigit() or int(length) > self.max_body):
                    resp = JSONResponse({"error": f"Request body larger than {self.max_body} bytes."}, status_code=413)
                    return await resp(scope, receive, send_logged)
                if scope["method"] == "POST":
                    receive = self._peek_tool(receive, status)
            await self.app(scope, receive, send_logged)
        finally:
            tool = f" tool={status['tool']}" if status["tool"] else ""
            log.info("%s %s %s %.0fms%s", scope["method"], path, status["code"], (time.monotonic() - started) * 1000, tool)

    def _peek_tool(self, receive, status):
        """Note the tool name of a tools/call request for the log (not its arguments)."""
        chunks = []

        async def wrapped():
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
                if not message.get("more_body") and sum(map(len, chunks)) <= self.max_body:
                    try:
                        body = json.loads(b"".join(chunks))
                        if isinstance(body, dict) and body.get("method") == "tools/call":
                            status["tool"] = str((body.get("params") or {}).get("name"))[:40]
                    except ValueError:
                        pass
            return message

        return wrapped


def build_app(server, store, limiter=None, allowed_hosts=None, trust_proxy=False):
    """The ASGI app: MCP at /mcp, plus / and /health."""

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        cached = store.source._cache.get("checker")
        return JSONResponse({"status": "ok", "data_as_of": cached[1].get("as_of") if cached else None})

    @server.custom_route("/", methods=["GET"])
    async def index(request: Request):
        return JSONResponse({
            "name": "Werkstudent Radar MCP server",
            "mcp_endpoint": "/mcp",
            "about": "Read-only statistics of Werkstudent job postings in Germany, updated daily.",
            "docs": "https://github.com/morty1338/werkstudent-radar#use-with-claude",
        })

    app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=MAX_BODY,
        transport_security=transport_security(allowed_hosts),
    )
    return Guard(app, limiter=limiter, trust_proxy=trust_proxy)
