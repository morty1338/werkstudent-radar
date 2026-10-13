#!/usr/bin/env bash
# Run a command and, if it fails, repeat its last 25 output lines as a GitHub error
# annotation. In a public repository annotations can be read without signing in
# (logs can't): GET /repos/<owner>/<repo>/check-runs/<job id>/annotations.
#   .github/scripts/run_logged.sh "Title" command [args...]
set -uo pipefail
title=$1
shift
log=$(mktemp)
"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ]; then
  message=$(tail -n 25 "$log" | sed $'s/\x1b\\[[0-9;]*[A-Za-z]//g' | tr -d '\r' | sed 's/%/%25/g' | awk 'BEGIN { ORS = "%0A" } { print }')
  echo "::error title=$title::$message"
fi
exit "$status"
