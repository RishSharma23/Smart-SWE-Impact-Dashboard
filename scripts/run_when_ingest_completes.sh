#!/usr/bin/env bash
#
# Wait for Phase 1's GitHub ingest to finish, then rebuild everything.
#
#   ./scripts/run_when_ingest_completes.sh          # foreground
#   nohup ./scripts/run_when_ingest_completes.sh &  # detached, survives logout
#
# The chain, in order, stopping at the first real failure:
#
#   1. wait for `impact ingest-github` to exit
#   2. confirm it actually finished rather than merely stopped   (see below)
#   3. normalize -> features -> validate -> export               (Phase 1 tail)
#   4. impact2 all --verify-content-hashes                       (Phase 2)
#
# Step 2 matters. A process disappearing is not the same as a run completing:
# it may have been killed, hit the rate limit, or crashed. The script checks
# the extraction-run ledger for a terminal status and refuses to build on a
# truncated dataset without saying so loudly.
#
# Progress is written to reports/phase2/auto_run_status.json so another process
# (or a person, or an agent picking this up later) can see where it got to
# without reading the log.

set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PY="${PY:-./.venv/bin/python}"
LOG_DIR="${LOG_DIR:-$ROOT/reports/phase2}"
LOG="$LOG_DIR/auto_run.log"
STATUS="$LOG_DIR/auto_run_status.json"
POLL_SECONDS="${POLL_SECONDS:-60}"
# Refuse to wait forever; 24h is far longer than a full extraction needs.
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-86400}"

mkdir -p "$LOG_DIR"

log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG"; }

status() {
  # $1 = phase, $2 = state, $3 = detail
  cat > "$STATUS" <<EOF
{
  "phase": "$1",
  "state": "$2",
  "detail": "$3",
  "updated_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "pid": $$,
  "log": "reports/phase2/auto_run.log"
}
EOF
}

fail() {
  log "FAILED during $1 — $2"
  status "$1" "failed" "$2"
  exit 1
}

# Only one of these at a time. A second copy would race the first over the
# same artifacts directory and produce a package neither of them describes.
LOCK="$LOG_DIR/.auto_run.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  existing=$(cat "$LOCK/pid" 2>/dev/null || echo "unknown")
  if kill -0 "$existing" 2>/dev/null; then
    log "another auto-run is already active (pid $existing); exiting"
    exit 0
  fi
  log "clearing a stale lock from pid $existing"
  rm -rf "$LOCK" && mkdir "$LOCK"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

log "=========================================================="
log "auto-run queued (pid $$). Polling every ${POLL_SECONDS}s."

# ---------------------------------------------------------------- 1. wait ---
status "waiting_for_ingest" "running" "polling for the ingest process to exit"
waited=0
if pgrep -f "impact ingest-github" >/dev/null 2>&1; then
  log "ingest is running; waiting for it to finish"
  while pgrep -f "impact ingest-github" >/dev/null 2>&1; do
    sleep "$POLL_SECONDS"
    waited=$((waited + POLL_SECONDS))
    if [ $((waited % 900)) -eq 0 ]; then
      shards=$(ls data/raw/github/pr_core 2>/dev/null | wc -l | tr -d ' ')
      detail=$(ls data/raw/github/pr_detail 2>/dev/null | wc -l | tr -d ' ')
      log "still waiting (${waited}s): pr_core=$shards pr_detail=$detail"
      status "waiting_for_ingest" "running" "pr_core=$shards pr_detail=$detail after ${waited}s"
    fi
    if [ "$waited" -ge "$MAX_WAIT_SECONDS" ]; then
      fail "waiting_for_ingest" "gave up after ${MAX_WAIT_SECONDS}s"
    fi
  done
  log "ingest process exited after ${waited}s"
else
  log "no ingest process running; proceeding immediately"
fi

# ------------------------------------------------- 2. did it actually finish ---
# A vanished process is not a completed run. Ask the ledger.
status "verifying_ingest" "running" "checking the extraction-run ledger"
INGEST_OK=$("$PY" - <<'PYEOF'
import json, pathlib, sys
path = pathlib.Path("data/raw/extraction_runs.json")
if not path.exists():
    print("no_ledger"); sys.exit(0)
runs = json.loads(path.read_text() or "[]")
github = [r for r in runs if str(r.get("stage", "")).startswith("ingest_github")]
if not github:
    print("no_github_run"); sys.exit(0)
last = github[-1]
print(f"{last.get('status', 'unknown')}")
PYEOF
)
log "last ingest_github run status: $INGEST_OK"
case "$INGEST_OK" in
  ok|partial)
    log "ingest reports '$INGEST_OK'; continuing"
    ;;
  *)
    # Continue anyway — a truncated dataset still produces a valid, honestly
    # labelled result, and Phase 2 records exactly what was missing. But say so.
    log "WARNING: ingest did not report a clean finish ('$INGEST_OK')."
    log "WARNING: building on a possibly truncated dataset. Phase 2 will record"
    log "WARNING: the gaps in coverage.json and the package will not be publishable."
    ;;
esac

# ------------------------------------------------------------ 3. Phase 1 tail ---
for stage in normalize features validate export; do
  log "--- phase 1: $stage ---"
  status "phase1_$stage" "running" "running impact $stage"
  if ! "$PY" -m impact "$stage" >>"$LOG" 2>&1; then
    # `validate` exits non-zero on a failed gate, which is information rather
    # than a reason to stop: the failure is recorded in quality_report.json and
    # propagates into the Phase 2 export as a known gap.
    if [ "$stage" = "validate" ]; then
      log "validate reported failing gates; continuing (they are recorded and exported)"
    else
      fail "phase1_$stage" "impact $stage exited non-zero"
    fi
  fi
done

# --------------------------------------------------------------- 4. Phase 2 ---
log "--- phase 2: all (with content-hash verification) ---"
status "phase2" "running" "running impact2 all --verify-content-hashes"
if ! "$PY" -m impact2 all --verify-content-hashes >>"$LOG" 2>&1; then
  fail "phase2" "impact2 all exited non-zero"
fi

# ------------------------------------------------------------------ report ---
SUMMARY=$("$PY" - <<'PYEOF'
import json, pathlib
m = pathlib.Path("artifacts/phase3/dashboard_manifest.json")
if not m.exists():
    print("no manifest produced"); raise SystemExit(0)
d = json.loads(m.read_text())
c = d.get("counts", {})
blockers = [b.get("item") for b in (d.get("publishable_blockers") or [])]
print(
    f"episodes={c.get('episodes')} engineers={c.get('engineers')} "
    f"rankable={c.get('rankable_engineers')} claims={c.get('claims')} "
    f"validation={d.get('validation_status')} publishable={d.get('publishable')} "
    f"blockers={blockers}"
)
PYEOF
)
log "PHASE 2 COMPLETE: $SUMMARY"
status "complete" "succeeded" "$SUMMARY"
log "Next: work the human queues in reports/phase2/audit_*.json, then"
log "      make p2-validate p2-export  (publishable flips to true when done)"
log "=========================================================="
