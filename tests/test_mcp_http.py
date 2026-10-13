"""The MCP server's remote mode (mcp/radar_http.py): a real uvicorn on a free port,
reached like a remote client would, with the repository's data."""

import json
import logging
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

pytest.importorskip("mcp.server")
uvicorn = pytest.importorskip("uvicorn")

import radar_http  # noqa: E402
import radar_server  # noqa: E402

INIT = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def limiter():
    return radar_http.RateLimiter(rate=0.5, burst=8, global_per_minute=1000)


@pytest.fixture(scope="module")
def base_url(limiter):
    radar_server.configure(["--http", "--data-dir", str(ROOT / "docs" / "data")])
    app = radar_http.build_app(radar_server.mcp, radar_server.store, limiter=limiter)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read())


def post(url, body, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers      # case-insensitive .get()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def refill(limiter):
    limiter.buckets.clear()


def test_index_and_health(base_url):
    assert get(base_url + "/")[1]["mcp_endpoint"] == "/mcp"
    status, body = get(base_url + "/health")
    assert status == 200 and body["status"] == "ok"


@pytest.mark.anyio
async def test_tools_over_http(base_url):
    from mcp import Client
    async with Client(f"{base_url}/mcp") as c:
        names = {t.name for t in (await c.list_tools()).tools}
        r = await c.call_tool("get_overview", {})
        typo = await c.call_tool("pay_stats", {"city": "Berln"})
    assert names == {"get_overview", "search_jobs", "pay_stats", "top_skills", "match_skills"}
    assert r.structured_content["postings"] > 0
    assert typo.is_error and "Did you mean: Berlin?" in typo.content[0].text
    assert get(base_url + "/health")[1]["data_as_of"] == r.structured_content["as_of"]


def test_body_limit(base_url):
    assert post(base_url + "/mcp", b"x" * (radar_http.MAX_BODY + 1))[0] == 413


def test_unknown_host_header_is_refused(base_url):
    assert post(base_url + "/mcp", INIT, {"Host": "evil.example"})[0] == 421


def test_rate_limit_per_client(base_url, limiter):
    codes = [post(base_url + "/mcp", INIT)[0] for _ in range(limiter.burst + 3)]
    assert codes[: limiter.burst].count(429) == 0 and codes[-1] == 429
    status, headers = post(base_url + "/mcp", INIT)
    assert status == 429 and int(headers.get("Retry-After")) >= 1
    assert get(base_url + "/health")[0] == 200          # health checks aren't limited


def test_log_has_no_client_address(base_url, caplog):
    with caplog.at_level(logging.INFO, logger="radar_http"):
        post(base_url + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "pay_stats", "arguments": {"city": "Leipzig"}}})
    lines = [r.getMessage() for r in caplog.records if r.name == "radar_http"]
    assert any(line.startswith("POST /mcp ") and line.endswith("tool=pay_stats") for line in lines)
    assert not any("127.0.0.1" in line or "Leipzig" in line for line in lines)


# --- Pieces on their own -----------------------------------------------------------------------

def test_rate_limiter_refills_and_caps_everyone():
    now = [0.0]
    rl = radar_http.RateLimiter(rate=1, burst=2, global_per_minute=5, clock=lambda: now[0])
    assert [rl.allow("a")[0] for _ in range(3)] == [True, True, False]
    now[0] += 1.0
    assert rl.allow("a") == (True, 0)                       # one token back after a second
    assert [rl.allow("b")[0] for _ in range(3)] == [True, True, False]
    assert rl.allow("c")[0] is False                        # 5 requests this minute: everyone waits
    now[0] += 60
    assert rl.allow("c") == (True, 0)


@pytest.mark.parametrize("env, protected, allowed", [
    ("", True, "localhost:*"),
    ("radar.example.com", True, "radar.example.com:*"),
    ("*", False, None),
])
def test_transport_security_from_env(env, protected, allowed):
    s = radar_http.transport_security(env)
    assert s.enable_dns_rebinding_protection is protected
    if allowed:
        assert allowed in s.allowed_hosts
