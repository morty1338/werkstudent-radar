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
    def lines():
        return [r.getMessage() for r in caplog.records if r.name == "radar_http"]

    def logged():
        return any(line.startswith("POST /mcp ") and line.endswith("tool=pay_stats") for line in lines())

    with caplog.at_level(logging.INFO, logger="radar_http"):
        post(base_url + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "pay_stats", "arguments": {"city": "Leipzig"}}})
        # The line is written after the response has gone out, so it can lag behind it.
        for _ in range(40):
            if logged():
                break
            time.sleep(0.05)
    assert logged()
    assert not any("127.0.0.1" in line or "Leipzig" in line for line in lines())


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


# --- The Vercel deployment ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def vercel_url(tmp_path_factory):
    """mcp/vercel/stage.sh's output, served the way Vercel serves it: `app` imported from
    api/index.py by uvicorn in its own process, every path sent to it (here with the
    repository's data instead of the live site)."""
    import os
    import shutil as sh
    import subprocess

    if sh.which("bash") is None:
        pytest.skip("bash not available")
    out = tmp_path_factory.mktemp("vercel") / "deploy"
    subprocess.run(["bash", str(ROOT / "mcp" / "vercel" / "stage.sh"), str(out)], check=True)
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert files == [".python-version", ".vercelignore", "api/index.py", "mcp/radar_data.py", "mcp/radar_http.py",
                     "mcp/radar_server.py", "mcp/radar_tools.py", "radar/__init__.py", "radar/stats.py",
                     "requirements.txt", "vercel.json"]
    config = json.loads((out / "vercel.json").read_text())
    assert config["rewrites"] == [{"source": "/(.*)", "destination": "/api/index"}] and "api/index.py" in config["functions"]
    assert ".env*" in (out / ".vercelignore").read_text()
    # Point the staged server at the repository's data instead of the network.
    app_py = out / "api" / "index.py"
    app_py.write_text(app_py.read_text().replace('configure(["--http"])', f'configure(["--http", "--data-dir", {str(ROOT / "docs" / "data")!r}])'))
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir", "api", "index:app", "--port", str(port), "--log-level", "warning"],
                            cwd=out, env={**os.environ, "PYTHONPATH": ""}, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            get(url + "/health")
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.mark.anyio
async def test_vercel_deployment_serves_the_tools(vercel_url):
    from mcp import Client
    assert get(vercel_url + "/health")[1]["status"] == "ok"
    async with Client(f"{vercel_url}/mcp") as c:
        r = await c.call_tool("pay_stats", {"city": "Berlin"})
    assert not r.is_error and r.structured_content["rates"] > 0
    # Any Host header passes behind the host's proxy.
    assert post(vercel_url + "/mcp", INIT, {"Host": "radar-mcp.vercel.app"})[0] == 200


def test_vercel_deployment_limits_each_client_by_its_forwarded_address(vercel_url):
    burst = radar_http.PER_CLIENT_BURST
    first = [post(vercel_url + "/mcp", INIT, {"X-Real-IP": "203.0.113.1"})[0] for _ in range(burst + 1)]
    assert first[-1] == 429
    assert post(vercel_url + "/mcp", INIT, {"X-Real-IP": "203.0.113.2"})[0] == 200
