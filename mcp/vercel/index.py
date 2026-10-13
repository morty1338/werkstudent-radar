"""Entrypoint for Vercel, which imports an ASGI `app` instead of starting a process.

mcp/vercel/stage.sh copies this file to api/index.py in the deployment, next to mcp/
and radar/; vercel.json sends every path to it (Vercel's runtime serves it with
uvicorn, lifespan included, and keeps the requested path). It is the same server as `radar_server.py --http`: the published data
cached for an hour, the same tools, limits and log, behind Vercel's proxy (which
sets the Host and X-Real-IP / X-Forwarded-For headers itself).
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))

import radar_http  # noqa: E402
import radar_server  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
radar_server.configure(["--http"])
app = radar_http.build_app(radar_server.mcp, radar_server.store, allowed_hosts="*", trust_proxy=True)
