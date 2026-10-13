"""Werkstudent Radar MCP server: lets Claude answer questions with the radar's job data.

Run (Claude Desktop / Claude Code start it for you, see the README):
    mcp/.venv/bin/python mcp/radar_server.py            # data from docs/data/ in this repo
    mcp/.venv/bin/python mcp/radar_server.py --live     # data from the published site

It speaks MCP over stdio: the client starts this script and exchanges JSON-RPC
messages on its stdin/stdout, so nothing else may be printed to stdout (logs go
to stderr). Read-only: it only reads the JSON files the website uses.
"""

import argparse
import logging
import sys
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

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
    """Run a tool on today's data; unknown names and missing data become messages for Claude."""
    try:
        return fn(store.data(), *args, **kwargs)
    except (rt.UnknownValue, radar_data.DataError) as e:
        raise ToolError(str(e)) from e


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


def configure(argv=None):
    """Pick the data source from the command line."""
    global store
    parser = argparse.ArgumentParser(description="Werkstudent Radar MCP server (stdio)")
    parser.add_argument("--live", action="store_true", help=f"read the published JSON from {radar_data.LIVE_URL}")
    parser.add_argument("--data-dir", default=radar_data.DATA_DIR, help="folder with summary.json, checker.json, postings.json")
    parser.add_argument("--ttl", type=int, default=radar_data.LIVE_TTL, help="seconds to keep live data in memory")
    args = parser.parse_args(argv)
    store = radar_data.Store(radar_data.Source(data_dir=args.data_dir, live=args.live, ttl=args.ttl))
    return args


def main(argv=None):
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    configure(argv)
    logging.getLogger("radar_server").info("data from %s", store.source.where)
    mcp.run()   # stdio


if __name__ == "__main__":
    main()
