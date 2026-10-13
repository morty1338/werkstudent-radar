#!/usr/bin/env bash
# Assemble the Vercel deployment of the MCP server in OUT_DIR: the entrypoint
# app.py at the root, the server in mcp/, the shared statistics code in radar/,
# the SDK as the only dependency. Nothing else of the repository goes out.
#   mcp/vercel/stage.sh OUT_DIR
set -euo pipefail
out=${1:?usage: mcp/vercel/stage.sh OUT_DIR}
root=$(cd "$(dirname "$0")/../.." && pwd)
rm -rf "$out"
mkdir -p "$out/mcp" "$out/radar"
cp "$root"/mcp/radar_*.py "$out/mcp/"
cp "$root/radar/__init__.py" "$root/radar/stats.py" "$out/radar/"
cp "$root/mcp/requirements.txt" "$out/requirements.txt"
cp "$root/mcp/vercel/app.py" "$root/mcp/vercel/vercel.json" "$out/"
echo "3.12" > "$out/.python-version"
