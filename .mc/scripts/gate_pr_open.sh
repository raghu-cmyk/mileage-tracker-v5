#!/usr/bin/env bash
# done_when gate helper: pass (exit 0) when the target repository has at least one
# OPEN pull request whose base branch is BASE.
#
# Usage: gate_pr_open.sh [BASE]
# Reads:  $MC_TARGET_REPO (owner/name), $GITHUB_TOKEN
#
# Domain-neutral: the concrete repo is provided via env, never hard-coded here.
set -uo pipefail

BASE="${1:-mc_webhook_test}"
REPO="${MC_TARGET_REPO:-}"
TOKEN="${GITHUB_TOKEN:-}"

if [ -z "$REPO" ]; then echo "gate_pr_open: MC_TARGET_REPO not set" >&2; exit 2; fi
if [ -z "$TOKEN" ]; then echo "gate_pr_open: GITHUB_TOKEN not set" >&2; exit 2; fi

COUNT=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/$REPO/pulls?state=open&base=$BASE&per_page=100" \
  | python3 -c "import sys,json
try:
    print(len(json.load(sys.stdin)))
except Exception:
    print(0)" 2>/dev/null)

COUNT="${COUNT:-0}"
echo "gate_pr_open: repo=$REPO base=$BASE open_prs=$COUNT"
[ "$COUNT" -ge 1 ]
