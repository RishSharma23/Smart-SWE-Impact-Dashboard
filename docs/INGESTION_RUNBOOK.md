# Ingestion Runbook

Operating, resuming and debugging a run.

---

## 1. Normal operation

```bash
make deps                    # once
cp .env.example .env         # set GITHUB_TOKEN, or: gh auth login
make all                     # full pipeline
make status                  # progress at any time
```

`make all` is safe to run repeatedly. Every stage is idempotent and every
GitHub request is cached by a hash of (method, url, body), so a rerun costs
**zero API budget** and produces byte-identical raw data.

### Stage order and why

```
ingest-git  →  ingest-github  →  normalize  →  graph  →  features  →  validate  →  export
```

`graph` may run any time after `ingest-git` (it only needs the clone).
`features` needs both `normalize` and `graph`. `export` copies whatever exists
and reports what is missing rather than failing.

---

## 2. Credentials

Resolution order: `GITHUB_TOKEN` env → `.env` → `gh auth token`.

Minimum grant: a **fine-grained PAT with Public repositories, read-only**.
No write scope is used anywhere in this pipeline. Create one at
<https://github.com/settings/personal-access-tokens/new>.

`.env` is git-ignored. `make secret-scan` verifies nothing token-shaped has
reached a tracked file.

---

## 3. Resuming an interrupted run

Just re-run the stage. Nothing needs cleaning up.

The guarantees behind that:

* **Response bodies are written to disk before the ledger row is recorded**, so
  an interrupt can lose at most the ledger's opinion of the last few pages —
  never a completed page's data.
* Batches are keyed by **PR-number bucket**, not by list position, so the same
  PR always lands in the same shard. Changing the window re-fetches only the
  buckets that actually changed.
* Discovery checkpoints per date slice in
  `data/raw/_checkpoints/discovery.json`.

Verify integrity after a resume:

```bash
make validate   # the resume_without_gaps gate checks the ledger for
                # duplicate request hashes and unexplained failures
```

---

## 4. Rate limits — what to expect

**The extraction is latency-bound, not budget-bound.** Measured against the
live API:

| Query | Rate-limit cost | Wall time |
|---|---|---|
| 25-PR aliased core batch | 1 point | ~5.1 s |
| 10-PR full review batch | 2 points | ~5 s |
| 100-result search page | 1 point | ~0.6 s |

The whole 90-day window costs roughly **4,500 of the 5,000 points/hour**
allowance, but takes 45–75 minutes because of server latency.

### Concurrency: use 2, not 12

GitHub's *secondary* limit is **90 CPU-seconds per 60 seconds of wall clock**.
At ~5 s of server work per request that sustains only ~1.5 concurrent
requests. Measured:

| `--workers` | Throughput | HTTP 403s |
|---|---|---|
| 2 | 22.8 req/min | ~0 |
| 4 | 32 req/min | intermittent, with 61 s backoffs |
| 12 | — | continuous 403 + `Retry-After: 60` |

The client honours every `Retry-After` exactly and recovers without data loss,
but time spent sleeping is time not fetching, and GitHub escalates repeated
secondary-limit violations. **2 is the default and the recommendation.**

Limits are always read from what GitHub returns (`rateLimit` block,
`x-ratelimit-*` headers), never hard-coded. The client pauses before hitting
zero, not after.

---

## 5. Common situations

**`No GitHub token available`**
Set `GITHUB_TOKEN` in `.env` or run `gh auth login`. See §2.

**Repeated `sleeping 61.0s (HTTP 403 …)`**
Secondary rate limit. Lower `--workers`. If it persists at `--workers 1`, the
token is in a penalty window — wait for `rateLimit.resetAt` in
`data/raw/github/_rate_limit_summary.json`.

**`offline mode: no cached response for …`**
`--offline` serves only from cache and fails loudly on a miss. That is
intended: it makes "did this run touch the network?" answerable. Drop the flag.

**Clone is huge / disk pressure**
The default `--shallow-since` clone is ~1.4 GB. Alternatives in
`config/repository.yaml`: `blob_none` (full DAG, blobs fetched lazily — much
smaller, but `numstat` becomes very slow) or `full`.

**A merged PR has no `pr_files` rows**
Its merge commit is outside the clone depth, or it merged to a non-default
branch. Check `pull_requests.has_merge_commit_in_clone`; the
`merge_commit_coverage` gate reports the total.

**Quality gate says `warn`, not `fail`**
`warn` means a coverage target was missed or a manual-audit queue has not been
filled in. `fail` means an invariant is broken. Both are listed in
`artifacts/quality_report.json` under `gates`.

---

## 6. Changing the window

```bash
# 365 days instead of 90 (widens the clone to match)
make window-year

# arbitrary window
python -m impact all --window-start 2025-08-17T00:00:00Z \
                     --window-end   2026-08-17T00:00:00Z

# full history
git -C data/raw/git/posthog fetch --unshallow
```

No schema change is required. Edit `window.lookback_days` in
`config/window.yaml` to change the default permanently.

Keep the clone at least ~30 days deeper than the window start, so PRs opened
before the window but merged inside it still have their merge commits locally
(`clone.shallow_since_buffer_days`).

---

## 7. Changing feature logic

Any edit that changes a derived **value** must bump the owning feature version
in **both** `src/impact/versions.py` and `config/feature_versions.yaml` — a
quality gate asserts they agree, and every derived row is stamped with its
version so mixed-version tables are detectable.

Tunables that change values (survival windows, proximity days, safety
vocabulary, acknowledgement patterns) live in
`config/feature_versions.yaml::parameters`, next to the versions they affect.

After a change:

```bash
make features validate       # rebuild and re-check
```

---

## 8. Reproducibility check

```bash
make normalize features validate     # records a hash baseline on first run
make normalize features validate     # second run compares against it
```

Table hashes are computed from **canonicalised row content**, not Parquet file
bytes — Parquet embeds a writer string that changes between runs. Operational
columns (`computed_at`, `run_id`, local paths) are excluded, so the check
genuinely asserts *"the same source SHA produced the same data"*.

---

## 9. What each output directory is

| Path | Committed? | Purpose |
|---|---|---|
| `data/raw/git/` | no | read-only clone of PostHog |
| `data/raw/github/` | no | immutable gzipped JSONL API responses + `_ledger.json` |
| `data/raw/_checkpoints/` | no | resume state |
| `data/normalized/` | no | 16 entity tables (Parquet + `.meta.json` sidecars) |
| `data/derived/` | no | 11 evidence tables |
| `data/samples/` | **yes** | 25-row fixtures so tests run without the clone |
| `artifacts/` | manifests only | the Phase 2 package |
| `schemas/` | **yes** | generated JSON Schema per table |
| `reports/` | **yes** | manual-audit queues with recorded verdicts |

Cleaning:

```bash
make clean-artifacts   # drop generated outputs, keep the clone and raw cache
```
