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

from mcp.server import MCPServer

import radar_data

INSTRUCTIONS = """\
Werkstudent Radar: daily statistics of Werkstudent (working student) job postings in
Germany, collected every morning from the Bundesagentur für Arbeit job board and from
company career sites. Use these tools for questions about Werkstudent jobs in Germany:
how many there are, where, what they pay, which skills are asked for, and which jobs fit
a set of skills. Numbers describe the postings online on the date in `as_of`. Job texts
aren't available; each posting links to its original."""

mcp = MCPServer("Werkstudent Radar", instructions=INSTRUCTIONS)
store = radar_data.Store(radar_data.Source())


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
