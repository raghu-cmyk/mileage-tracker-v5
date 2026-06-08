#!/usr/bin/env bash
# done_when gate helper: pass (exit 0) when the target repository has at least
# MIN GitHub issues (excluding pull requests).
#
# Usage: gate_min_issues.sh [MIN]
# Reads:  $MC_TARGET_REPO (owner/name), $GITHUB_TOKEN
#
# Domain-neutral: the concrete repo is provided via env, never hard-coded here.
set -uo pipefail

MIN="${1:-3}"
REPO="${MC_TARGET_REPO:-}"
TOKEN="${GITHUB_TOKEN:-}"

if [ -z "$REPO" ]; then echo "gate_min_issues: MC_TARGET_REPO not set" >&2; exit 2; fi
if [ -z "$TOKEN" ]; then echo "gate_min_issues: GITHUB_TOKEN not set" >&2; exit 2; fi

COUNT=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/$REPO/issues?state=all&per_page=100" \
  | python3 -c "import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    print(0); raise SystemExit
print(sum(1 for i in d if isinstance(i,dict) and 'pull_request' not in i))" 2>/dev/null)

COUNT="${COUNT:-0}"
echo "gate_min_issues: repo=$REPO issues=$COUNT min=$MIN"
[ "$COUNT" -ge "$MIN" ]
