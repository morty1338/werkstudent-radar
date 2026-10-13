#!/usr/bin/env bash
# Deploy the MCP server to Vercel; called by .github/workflows/mcp-deploy.yml.
# Needs VERCEL_TOKEN. Creates the project on the first run, deploys the staged
# folder (outside the git checkout, so `vercel link` has no repository to connect)
# as production, and writes the production address to $GITHUB_ENV as MCP_URL.
set -euo pipefail
cli=${VERCEL_CLI:-vercel@61.1.0}
project=${PROJECT:-radar-mcp}

token=$(printf '%s' "${VERCEL_TOKEN:?VERCEL_TOKEN is not set}" | tr -d '\r\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
case "$token" in
  *[[:space:]]*) echo "The VERCEL_TOKEN secret contains a space. Paste only the token itself (one word, no name, no 'Bearer')." >&2; exit 1 ;;
esac
if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::add-mask::$token"; fi

build="${RUNNER_TEMP:-$(mktemp -d)}/radar-mcp"
bash "$(dirname "$0")/stage.sh" "$build"
cd "$build"
vc() { npx --yes "$cli" "$@" --token "$token" --no-color; }

vc project add "$project" > /dev/null 2>&1 || true    # fails when it exists already
vc link --yes --project "$project"
rm -f .env.local .gitignore                           # written by `link`, never deployed
vc deploy --prod --yes

# The production domain (deployment-specific URLs can sit behind Vercel's login).
team=$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['orgId'])")
domain=$(curl -fsS -H "Authorization: Bearer $token" "https://api.vercel.com/v9/projects/$project/domains?teamId=$team" \
  | python3 -c "import json, sys; names = [d['name'] for d in json.load(sys.stdin)['domains'] if not d.get('redirect')]; print(min(names, key=len))")
echo "Production address: https://$domain"
if [ -n "${GITHUB_ENV:-}" ]; then echo "MCP_URL=https://$domain" >> "$GITHUB_ENV"; fi
