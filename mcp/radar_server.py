"""Werkstudent Radar MCP server: lets Claude answer questions with the radar's job data.

Run (Claude Desktop / Claude Code start it for you, see the README):
    mcp/.venv/bin/python mcp/radar_server.py            # stdio, data from docs/data/ in this repo
    mcp/.venv/bin/python mcp/radar_server.py --live     # stdio, data from the published site
    mcp/.venv/bin/python mcp/radar_server.py --http     # Streamable HTTP on :8000/mcp, live data

Locally it speaks MCP over stdio: the client starts this script and exchanges
JSON-RPC messages on its stdin/stdout, so nothing else may be printed to stdout
(logs go to stderr). With --http it is a web server anyone can connect to by URL
(radar_http.py: rate limits, body limit, /health); its data is the published
JSON, refreshed hourly. Read-only either way: it only reads the website's files.
"""

import argparse
import json
import logging
import os
import sys
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

import radar_data
import radar_tools as rt

INSTRUCTIONS = """\
Werkstudent Radar: daily statistics of Werkstudent (working student) job postings in
Germany, collected every morning from the Bundesagentur für Arbeit job board and from
company career sites. Use these tools for questions about Werkstudent jobs in Germany:
how many there are, where, what they pay, which skills are asked for, and which jobs fit
a set of skills. Numbers describe the postings online on the date in `as_of`. Job texts
aren't available; each posting links to its original."""

mcp = MCPServer("Werkstudent Radar", instructions=INSTRUCTIONS)
store = radar_data.Store(radar_data.Source())


def answer(fn, *args, **kwargs):
    """Run a tool on today's data; unknown names and missing data become messages for Claude.

    The text the model reads is compact JSON (the SDK would indent it, which costs tokens);
    the same dict goes out as structured content and matches the declared dict return type."""
    try:
        data = fn(store.data(), *args, **kwargs)
    except (rt.UnknownValue, radar_data.DataError) as e:
        raise ToolError(str(e)) from e
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


@mcp.tool()
def get_overview() -> dict[str, Any]:
    """Headline numbers of the Werkstudent job market in Germany today: number of postings,
    date of the last update, median hourly pay with its 95% confidence interval, the top
    cities, fields and skills. Start here for general questions ("how many Werkstudent jobs
    are there?", "what does a Werkstudent earn?")."""
    return answer(rt.get_overview)


@mcp.tool()
def search_jobs(city: str | None = None, field: str | None = None, skill: str | None = None,
                no_german: bool = False, limit: int = 10) -> dict[str, Any]:
    """Find Werkstudent postings online today, newest first, with title, company, city,
    field, hourly pay if stated, German requirement and the link to the original posting.

    city: a German city, e.g. "Berlin", "München" or "Munich".
    field: a job field id or name, e.g. "it", "data", "marketing", "Finance & Controlling".
    skill: one skill, e.g. "Python", "SQL", "Excel", "SAP".
    no_german: only postings that don't require German.
    limit: how many postings to return (1–50); `matches` gives the total."""
    return answer(rt.search_jobs, city=city, field=field, skill=skill, no_german=no_german, limit=limit)


@mcp.tool()
def pay_stats(field: str | None = None, city: str | None = None) -> dict[str, Any]:
    """Hourly pay of Werkstudent postings, optionally for one field and/or city: median,
    middle half (25th–75th percentile), 95% confidence interval and how many postings state
    a rate. `reliable` is false when there are fewer than 10 stated rates (or fewer than 5
    employers, or a very wide interval); then say the numbers are only anecdotal."""
    return answer(rt.pay_stats, field=field, city=city)


@mcp.tool()
def top_skills(field: str | None = None, city: str | None = None, limit: int = 10) -> dict[str, Any]:
    """The skills most often asked for in Werkstudent postings, optionally for one field
    and/or city, with the number and share of postings that ask for each."""
    return answer(rt.top_skills, field=field, city=city, limit=limit)


@mcp.tool()
def match_skills(skills: list[str], city: str | None = None, field: str | None = None,
                 examples: int = 3) -> dict[str, Any]:
    """How many Werkstudent postings someone with these skills qualifies for (a posting fits
    when at most a quarter of its listed skills are missing), the share of postings that list
    skills, the next 3 skills that would make the most extra postings fit, and the newest
    fitting postings with links (`examples`, 0–10). Use it for "which jobs fit my skills?" or
    "what should I learn next?". Optional city and field filters."""
    return answer(rt.match_skills, skills, city=city, field=field, examples=examples)


HTTP_TTL = 3600   # the data changes once a day; an hour keeps a remote server close to it


def configure(argv=None):
    """Pick the transport and the data source from the command line."""
    global store
    parser = argparse.ArgumentParser(description="Werkstudent Radar MCP server (stdio, or Streamable HTTP with --http)")
    parser.add_argument("--live", action="store_true", help=f"read the published JSON from {radar_data.LIVE_URL}")
    parser.add_argument("--data-dir", help="folder with summary.json, checker.json, postings.json (default: docs/data)")
    parser.add_argument("--ttl", type=int, help="seconds to keep live data in memory (stdio 600, HTTP 3600)")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio (live data unless --data-dir)")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="HTTP address (0.0.0.0 in a container)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="HTTP port (or $PORT)")
    args = parser.parse_args(argv)
    live = args.live or (args.http and not args.data_dir)
    ttl = args.ttl or (HTTP_TTL if args.http else radar_data.LIVE_TTL)
    store = radar_data.Store(radar_data.Source(data_dir=args.data_dir or radar_data.DATA_DIR, live=live, ttl=ttl))
    return args


def main(argv=None):
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    args = configure(argv)
    logging.getLogger("radar_server").info("data from %s", store.source.where)
    if not args.http:
        mcp.run()   # stdio
        return
    import uvicorn

    import radar_http
    app = radar_http.build_app(mcp, store)
    # Behind a hosting proxy the client address comes from X-Forwarded-For (FORWARDED_ALLOW_IPS="*"
    # in the container). uvicorn's access log is off: it would record client addresses.
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                limit_concurrency=50, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
