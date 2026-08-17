#!/usr/bin/env bash
# Wait for the GitHub ingest to finish, then run the rest of Phase 1 end to end
# and render the quality report. Safe to re-run: every stage is idempotent.
set -uo pipefail
cd "$(dirname "$0")/.."

PY=./.venv/bin/python
export PYTHONPATH=src

echo "=== waiting for ingest-github to finish ==="
while pgrep -f "impact ingest-github" >/dev/null; do sleep 60; done
echo "ingest finished at $(date -u +%FT%TZ)"
echo "pr_core=$(ls data/raw/github/pr_core 2>/dev/null | wc -l | tr -d ' ') \
pr_detail=$(ls data/raw/github/pr_detail 2>/dev/null | wc -l | tr -d ' ') \
issues=$(ls data/raw/github/issues 2>/dev/null | wc -l | tr -d ' ')"

# If the ingest exited before completing every pass, resume it once.
if [ "$(ls data/raw/github/issues 2>/dev/null | wc -l | tr -d ' ')" -eq 0 ]; then
  echo "=== issues pass incomplete; resuming ingest (cached work is free) ==="
  $PY -m impact ingest-github --workers 2
fi

for stage in normalize ingest-web graph features validate export; do
  echo "=== $stage ==="
  $PY -m impact "$stage" || echo "!! stage $stage exited non-zero"
done

echo "=== rendering quality report ==="
$PY scripts/render_quality_report.py

echo "=== unit + contract tests ==="
./.venv/bin/pytest -q 2>&1 | tail -5

echo "=== integration tests (real artifacts) ==="
./.venv/bin/pytest -q -m integration 2>&1 | tail -20

echo "=== DONE $(date -u +%FT%TZ) ==="
