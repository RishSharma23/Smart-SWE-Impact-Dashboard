#!/usr/bin/env bash
#
# Watch the Phase 2 export and redeploy the dashboard whenever it changes.
#
# Phase 2 rewrites artifacts/phase3/ at the end of every run. This polls the
# manifest's sha256 set and, when it changes AND the package is internally
# consistent, rebuilds and republishes the site. It is deliberately conservative:
#
#   * it waits for the manifest to stop changing before building, so it never
#     reads a half-written export;
#   * it verifies every file against the manifest first (the build does this too,
#     and refuses to publish a partial copy);
#   * a failed build leaves the previous deployment untouched.
#
# Usage:
#   scripts/watch-phase2.sh                 # poll every 60s, forever
#   INTERVAL=300 scripts/watch-phase2.sh    # poll every 5 minutes
#   scripts/watch-phase2.sh --once          # deploy if changed, then exit
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MANIFEST="artifacts/phase3/dashboard_manifest.json"
STATE_FILE=".git/phase3-deployed-fingerprint"
INTERVAL="${INTERVAL:-60}"
ONCE=false
[[ "${1:-}" == "--once" ]] && ONCE=true

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }

# A fingerprint of the whole package, not just its mtime: Phase 2 can rerun and
# produce byte-identical output, which is not a reason to redeploy.
fingerprint() {
  [[ -f "$MANIFEST" ]] || return 1
  python3 - "$MANIFEST" <<'PY' 2>/dev/null
import hashlib, json, sys
try:
    m = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
files = m.get("files") or {}
if not files:
    sys.exit(1)
h = hashlib.sha256()
h.update(str(m.get("generated_at")).encode())
for name in sorted(files):
    h.update(name.encode())
    h.update(str(files[name].get("sha256")).encode())
print(h.hexdigest())
PY
}

deployed="$(cat "$STATE_FILE" 2>/dev/null || echo none)"
log "watching $MANIFEST (poll ${INTERVAL}s); last deployed fingerprint: ${deployed:0:12}"

while true; do
  current="$(fingerprint)" || current=""

  if [[ -z "$current" ]]; then
    log "no readable export yet — waiting"
  elif [[ "$current" == "$deployed" ]]; then
    : # unchanged; say nothing, this loop is meant to be quiet
  else
    log "export changed (${current:0:12}) — settling"
    # Wait for two identical reads 20s apart so we never build a partial write.
    settled=false
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      sleep 20
      again="$(fingerprint)" || again=""
      if [[ -n "$again" && "$again" == "$current" ]]; then settled=true; break; fi
      log "still being written — waiting"
      current="$again"
    done

    if [[ "$settled" != true ]]; then
      log "export never settled; will retry next poll"
    else
      log "building and deploying"
      if scripts/deploy-dashboard.sh "${IMPACT_PUBLISH_APPROVAL:-}" >> /tmp/phase3-autodeploy.log 2>&1; then
        printf '%s' "$current" > "$STATE_FILE"
        deployed="$current"
        log "deployed ${current:0:12}  (log: /tmp/phase3-autodeploy.log)"
      else
        log "DEPLOY FAILED — previous site left in place. See /tmp/phase3-autodeploy.log"
        tail -20 /tmp/phase3-autodeploy.log
      fi
    fi
  fi

  [[ "$ONCE" == true ]] && break
  sleep "$INTERVAL"
done
