#!/usr/bin/env bash
#
# Build the dashboard from the current Phase 2 export and publish it to GitHub
# Pages.
#
# Why a branch and not a CI build: the Phase 2 export is ~190 MB of generated
# JSON and is not committed (see docs/DEPLOYMENT_RUNBOOK.md), so GitHub Actions
# has no data to build from. The site is therefore built here, where the data
# lives, and only the built output is pushed — to an orphan `gh-pages` branch
# that is force-replaced on every deploy so the repository does not accumulate
# a few hundred megabytes of HTML per run.
#
# Usage:  scripts/deploy-dashboard.sh ["approval note"]
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BRANCH="gh-pages"
DATA_DIR="${IMPACT_DATA_DIR:-artifacts/phase3}"
BASE_PATH="${NEXT_PUBLIC_BASE_PATH:-/Smart-SWE-Impact-Dashboard}"
APPROVAL="${1:-${IMPACT_PUBLISH_APPROVAL:-}}"

if [[ ! -f "$DATA_DIR/dashboard_manifest.json" ]]; then
  echo "error: no Phase 2 export at $DATA_DIR — run 'make p2-export' first." >&2
  exit 1
fi

GENERATED_AT="$(python3 -c "import json;print(json.load(open('$DATA_DIR/dashboard_manifest.json'))['generated_at'])")"
EPISODES="$(python3 -c "import json;print(json.load(open('$DATA_DIR/dashboard_manifest.json'))['counts']['episodes'])")"
HEAD_SHA="$(python3 -c "import json;print(json.load(open('$DATA_DIR/dashboard_manifest.json'))['source']['analyzed_head_sha'])")"

echo "==> building from $DATA_DIR (export generated $GENERATED_AT, $EPISODES episodes)"

cd web
export NODE_OPTIONS="--max-old-space-size=6144"
export IMPACT_DATA_DIR="$DATA_DIR"
export NEXT_PUBLIC_BASE_PATH="$BASE_PATH"
export IMPACT_PUBLISH_APPROVAL="$APPROVAL"
export NEXT_TELEMETRY_DISABLED=1

rm -rf out
npm run data
npx next build
touch out/.nojekyll          # Pages must not run Jekyll over _next/
cd "$REPO_ROOT"

PAGES="$(find web/out -name index.html | wc -l | tr -d ' ')"
echo "==> built $PAGES pages, $(du -sh web/out | cut -f1)"

# Refuse to publish anything GitHub will reject outright.
if find web/out -type f -size +99M | grep -q .; then
  echo "error: an output file exceeds GitHub's 100 MB limit:" >&2
  find web/out -type f -size +99M >&2
  exit 1
fi

echo "==> publishing to $BRANCH"
WORKTREE="$(mktemp -d)"
# Unique per run. A fixed staging branch survives an interrupted deploy, and the
# next run then dies on `checkout --orphan: a branch named ... already exists` —
# silently, because that command's output is discarded. This deploy failed
# exactly that way once.
STAGING="$BRANCH-staging-$$"
trap 'git worktree remove --force "$WORKTREE" 2>/dev/null || true; rm -rf "$WORKTREE"; git branch -D "$STAGING" 2>/dev/null || true' EXIT

# An orphan commit each time: the site is a snapshot, not an accumulating history.
git worktree add --detach "$WORKTREE" >/dev/null
(
  cd "$WORKTREE"
  # Not silenced: if this fails the deploy must say so, not exit blank.
  git checkout --orphan "$STAGING" >/dev/null
  git rm -rq --cached . 2>/dev/null || true
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -R "$REPO_ROOT/web/out/." .
  git add -A
  git -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name)}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email)}" \
      commit -q -m "Deploy dashboard: export ${GENERATED_AT}, ${EPISODES} episodes, repo @ ${HEAD_SHA:0:12}

Built from Phase 2 export generated ${GENERATED_AT}.
Analysed commit: ${HEAD_SHA}
Pages: ${PAGES}
Base path: ${BASE_PATH}"
  git push -q --force origin "HEAD:refs/heads/$BRANCH"
)

echo "==> deployed. https://rishsharma23.github.io${BASE_PATH}/"
