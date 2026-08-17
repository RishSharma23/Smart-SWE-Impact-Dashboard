# Phase 1 Handover — PostHog Engineering Impact Dashboard

**Data ingestion and readiness foundation.**
Pipeline version `1.0.0` · schema version `1.0.0` · extractor version `1.0.0`

> **Status: COMPLETE.** The real 90-day extraction and validation have both
> finished. Every number below is measured from that run, not projected.
> Quality status is `pass` (13/13 gates, 172/172 invariant checks) and the
> Phase 2 contract is **frozen** at schema `1.0.0`
> ([`docs/PHASE_2_CONTRACT.md`](docs/PHASE_2_CONTRACT.md)).

---

## 1. Source, window, and provenance

| | |
|---|---|
| Repository | `https://github.com/PostHog/posthog` (read-only clone; never modified, never built) |
| Repository qualifier | `github.com/PostHog/posthog` |
| Default branch | `master` |
| **Analyzed HEAD SHA** | `d4295d5794f95a0ae726edd0e27450115f3fc0a3` |
| HEAD committed at | `2026-08-17T02:43:54Z` |
| **Analysis window** | `2026-05-19T00:00:00Z` → extraction start (**90 complete UTC days**) |
| Clone strategy | `--shallow-since=2026-04-19 --single-branch --branch master --no-tags` |
| Clone size on disk | 1.4 GB · 15,485 commits · 43,948 tracked files |
| Linear history verified | **yes** — 15,484 single-parent commits + 1 shallow-graft root |

The clone reaches 30 days further back than the window. That buffer is **not**
analysis data: it exists so a PR opened before 2026-05-19 but *merged inside*
the window still has its merge commit available locally. Window membership is
always decided from timestamps, never from clone depth.

---

## 2. How to reproduce, and how to extend

```bash
# One-time setup
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then set GITHUB_TOKEN, or just `gh auth login`

make all                      # clone → ingest → normalize → graph → features → validate → export
```

Individual stages, each independently rerunnable and resumable:

```bash
make ingest-git               # clone/verify + commits + diffs        (~66 s)
make ingest-github            # discovery + PR core + review detail   (network-bound)
make normalize                # raw → normalized entity tables
make graph                    # module dependency graph               (~170 s)
make features                 # deterministic evidence features
make validate                 # invariants, reconciliation, gates
make export                   # artifacts/ package + run manifest
make test                     # unit + contract tests (no network, no clone)
```

**Extending the window — no schema change required.** The window is config, and
every table is keyed by stable IDs:

```bash
# One year
./.venv/bin/python -m impact all --window-start 2025-08-17T00:00:00Z

# Or edit config/window.yaml:  window.lookback_days: 365
# Then widen the clone to match:
git -C data/raw/git/posthog fetch --shallow-since=2025-07-17
# Full history instead:
git -C data/raw/git/posthog fetch --unshallow
```

Because request batches are bucketed on PR number rather than list position
(`_number_buckets`), widening the window re-fetches only the buckets that
actually changed — it does not invalidate the existing cache.

---

## 3. Environment and accounts

* Python 3.12.1, macOS arm64 (MacBook Air M1). Dependencies pinned in
  `requirements.txt`; no PostHog dependency is installed and no PostHog code is
  executed.
* **No account was created.** Authentication reuses the operator's already
  authenticated GitHub CLI.
* **Secret names only** (values never appear in Git, generated data, logs, or
  this document):

  | Name | Purpose | Minimum grant |
  |---|---|---|
  | `GITHUB_TOKEN` | GitHub GraphQL + REST reads | Fine-grained PAT, **Public repositories, read-only**. No write scope is used anywhere. |

  Resolution order: `GITHUB_TOKEN` env → `.env` → `gh auth token`.
  `.env` is git-ignored; `.env.example` documents the shape.

---

## 4. What was extracted **[MEASURED — run complete]**

Source SHA `d4295d5794f95a0ae726edd0e27450115f3fc0a3`, window 2026-05-19T00:00:00Z → 2026-08-17T14:58:45Z
(90 complete UTC days). **Quality status: `pass` — 13/13 gates,
172/172 invariant checks.**

### Row counts (917,914 rows across 28 tables)

| Table | Rows |
|---|---:|
| `actors` | 934 |
| `candidate_episode_edges` | 30,619 |
| `candidate_episodes` | 2,035 |
| `comments` | 103,393 |
| `commit_parents` | 15,484 |
| `commits` | 15,485 |
| `component_edges` | 1,651 |
| `components` | 122 |
| `dependency_edges` | 141,389 |
| `extraction_runs` | 28 |
| `feature_flags` | 1,049 |
| `issues` | 872 |
| `module_nodes` | 36,412 |
| `path_map` | 43,595 |
| `pr_anomalies` | 23,855 |
| `pr_blast_radius` | 23,855 |
| `pr_change_shape` | 23,855 |
| `pr_files` | 126,552 |
| `pr_regression_candidates` | 23,855 |
| `pull_requests` | 23,855 |
| `raw_pages` | 5,996 |
| `references` | 45,320 |
| `review_comments` | 61,538 |
| `review_intervention_candidates` | 61,538 |
| `review_threads` | 38,789 |
| `reviewer_intervention_rollup` | 112 |
| `reviews` | 65,609 |
| `web_artifacts` | 117 |

### Quality gates

| Gate | Result |
|---|---|
| `reproducibility` | **PASS** |
| `resume_without_gaps` | **PASS** |
| `conventional_title_fixtures` | **PASS** |
| `identity_fixtures` | **PASS** |
| `window_boundary` | **PASS** |
| `merge_commit_coverage` | **PASS** |
| `change_size_reconciliation` | **PASS** |
| `independent_pr_count` | **PASS** |
| `audit_queue:stratified_prs` | **PASS** |
| `audit_queue:regression_candidates` | **PASS** |
| `audit_queue:review_interventions` | **PASS** |
| `graph_parser_coverage` | **PASS** |
| `secret_scan` | **PASS** |

### Headline feature statistics

* **Blast radius (ranking-eligible PRs):** {"component": 7542, "cross_product": 1089, "local": 1265, "platform_wide": 3137, "unknown": 285}; unknown rate 2.1%
* **Anomalies:** 420 PRs carry a data-quality flag (1.8%); GitHub-vs-Git change-size agreement **99.74%** over 13,086 reconcilable PRs
* **Regression candidates:** {"explicit": 1476, "linked": 34, "none": 15019, "proximate": 7326}; 7,326 require human confirmation
* **Review interventions:** 5,962 candidates from 61,538 comments (46.4% substantive); 19,963 name a safety concern
* **Episodes:** 30,619 candidate edges → 1,602 episodes covering 5,403 PRs (largest 55, median 2)

### API cost (cumulative across all runs)

| Metric | Value |
|---|---:|
| Requests issued | 5,996 |
| GraphQL points spent | 8,330 |
| Seconds in flight | 16,692 |
| Requests needing a retry | 56 |
| By entity | {"discovery": 735, "issues": 517, "pr_core": 1357, "pr_detail": 2599, "pr_overflow": 787, "repository": 1} |

Total money cost: **$0.** Wall clock for the GitHub stage: **2h 03m** at 2
workers — latency-bound, not budget-bound (8,330 points spent against a
5,000/hour allowance spread over two hours).

### Known gaps

* **shallow_clone** — clone strategy shallow_since; oldest available commit 2026-08-17T02:43:54Z. Survival and reachability look forward only. → _has_merge_commit_in_clone=false PRs have no pr_files rows_
* **unparsed_languages** — 1774 files in ['go', 'hog', 'rb', 'rust', 'sql'] have no import parser → _reachability_band='unknown' for changes confined to them_
* **dynamic_imports_invisible** — module_nodes.has_dynamic_imports flags affected files → _graph reach is a lower bound_
* **review_thread_pagination** — 27 threads truncated at the pagination cap → _comment-level evidence incomplete on those threads_
* **survival_beyond_window** — survival_* is NULL with a reason when the checkpoint falls after the window end → _never read a NULL survival as 0_
* **review_comment_semantics** — semantic consequence of review comments is Phase 2 work → _Phase 1 emits candidates only_

---

## 5. Title-prefix distribution and observed exceptions **[MEASURED, 3,000-commit sample]**

| Prefix | Count | Share |
|---|---:|---:|
| `fix` | 1,430 | 47.7% |
| `feat` | 1,130 | 37.7% |
| `chore` | 351 | 11.7% |
| `refactor` | 39 | 1.3% |
| `perf` | 27 | 0.9% |
| `docs` | 11 | 0.4% |
| `revert` | 5 | 0.2% |
| `ci` | 3 | 0.1% |
| `test` | 2 | 0.1% |
| non-conventional | 2 | 0.07% |

**Exceptions the parser handles explicitly:**

* **Reverts are conventional, not git-style.** PostHog spells them
  `revert(scope): <subject>`, not `Revert "<subject>"`. Matching only git's form
  reported **zero** reverts on this repository — a defect found and fixed during
  this phase (`REVERT_CONVENTIONAL_RE` in `src/impact/ingest/git_source.py`).
* **Merge-queue titles are not human titles.** `trunk-merge/pr-83501/<uuid>` is
  classified `merge_queue_artifact` with parser confidence forced to `0.0`.
* Uppercase types, `[bracket]` leaders, alias types (`feature`→`feat`), missing
  subjects and unknown types each lower confidence with a recorded reason
  rather than being silently normalised.
* Because ~99.9% of titles are conventional, **the prefix is near-useless as a
  discriminator**. `pr_change_shape.title_claim_corroborated`, which checks the
  prefix against the paths actually touched, is the signal worth using.

---

## 6. Component mapping and ownership **[MEASURED]**

Priority order implemented literally, with the answering priority recorded on
every path (`component_rule_priority`):

| Priority | Source | Found at HEAD |
|---|---|---|
| 1 | `products/*/manifest.tsx` product manifests | 84 product directories |
| 2 | Distributed `owners.yaml` (nearest enclosing dir) | 26 files |
| 2 | Nearest `AGENTS.md` (path-local instructions) | 44 files |
| 3 | `.github/CODEOWNERS` | present; `CODEOWNERS-soft` absent |
| 4 | Conventions in `config/components.yaml` | 40 rules |
| 5 | Language-aware module graph | 141,389 edges |
| 6 | `unknown` | reported, never hidden |

PostHog's real ownership lives in the **distributed `owners.yaml` system**, not
in CODEOWNERS — the CODEOWNERS file explicitly argues against its own use
("Adding entries to the codeowners file is an anti-social … thing to do"). The
resolver implements PostHog's own semantics: nearest enclosing file wins,
`/x/` anchors to that file's directory, bare `x/` matches at any depth, last
matching rule wins.

`ee/**` is tagged `license_area = "PostHog Enterprise (proprietary)"` and is
never aggregated as ordinary MIT code.

Path map: **43,595 distinct paths** resolved; coverage in `artifacts/quality_report.json`.

---

## 7. Bot, generated, and imported-change handling **[MEASURED]**

Bots observed authoring commits in the window:
`posthog[bot]` (541 commits), `mendral-app[bot]` (166), `dependabot[bot]`,
`tests-posthog[bot]`, `scheduled-actions-posthog[bot]`,
`clickhouse-sync-posthog[bot]`, `releaser-posthog-cli[bot]`,
`posthog-js-upgrader[bot]`, `inkeep[bot]`, plus `trunk-io` as merge actor.

* Classification yields a **probability plus reasons**, never a silent drop.
  `[bot]` login suffix and GraphQL `__typename == Bot` are authoritative (1.0);
  name-shaped guesses cap at 0.75 and are marked
  `bot_classification_uncertain`.
* **AI co-authorship is tracked on a separate axis.** 1,855 commits carry
  `Co-authored-by: … noreply@anthropic.com` and 48 carry `cursoragent@cursor.com`.
  These are *human-authored PRs written with assistance*, so they are recorded
  as `has_ai_co_author` and never folded into `bot_probability`.
* Generated / snapshot / lockfile / vendored / migration paths are **labelled,
  not filtered** — a lockfile-only PR stays in the dataset with
  `lockfile_only` in `anomaly_flags`.
* Identity clustering merges only on evidence that is actually unique (GitHub
  login, normalised email, the numeric id inside
  `12345+login@users.noreply.github.com`). Display name is **not** a merge key.
  An email mapping to several logins is recorded as `shared_email` ambiguity
  instead of merging strangers.

---

## 8. API cost and rate-limit behaviour **[MEASURED]**

* Authenticated GraphQL allowance: 5,000 points/hour. Limits are read from the
  `rateLimit` block and `x-ratelimit-*` headers, never hard-coded.
* **A 25-PR aliased batch costs 1 point** (nodeCount 500); a 10-PR full review
  batch costs 2 (nodeCount 1,400); a 100-result search page costs 1. Total
  projected spend for the window is **well under one hour's allowance** — the
  extraction is *latency*-bound, not budget-bound.
* Measured latency ≈ 5.1 s per 25-PR batch regardless of which fields are
  requested (verified by A/B-ing `participants`, `bodyText`,
  `closingIssuesReferences`).
* **Concurrency is capped at 4 workers.** 12 workers reliably triggered
  GitHub's *secondary* rate limit (HTTP 403 + `Retry-After: 60`). The client
  honoured every `Retry-After` exactly and recovered without data loss, which
  is the behaviour the spec asks for — but 4 is the sustainable setting.
  Override with `--workers N`.
* Exponential backoff with full jitter on 403/429/5xx; `RATE_LIMITED` GraphQL
  errors (which arrive as HTTP 200) are handled separately.

Cumulative totals: **5,996 requests, 8,330 points, 56 needing a retry** — see §4 and `artifacts/run_manifest.json.api_cost`.

---

## 9. Quality gates **[PENDING RUN]**

Run `make validate`. Results land in `artifacts/quality_report.json` and are
mirrored into `run_manifest.json.quality_gates`. Gates implemented:

reproducibility (two runs → identical content hashes) · resume-without-gaps ·
window-boundary correctness · conventional-title fixtures · identity fixtures ·
30 stratified PRs manually inspected · 10 regression candidates checked ·
10 review-intervention candidates checked · graph/parser coverage reported ·
PR counts reconciled against independent GitHub queries · per-table schema,
null-semantics, row-count, uniqueness and foreign-key checks · secret scan.

---

## 10. Known defects, uncertain inferences, deferred work

**Structural limits (documented, not bugs):**

1. **Shallow clone.** No history before 2026-04-19. A PR whose merge commit
   predates that has `has_merge_commit_in_clone = false` and no `pr_files`
   rows. Survival and reachability look *forward* only. Fix: `git fetch --unshallow`.
2. **Rust / Go / SQL / Hog / Ruby imports are not parsed** (1,774 files). Blast
   radius for changes confined to those languages is `unknown`, never `local`.
3. **Dynamic imports and runtime registries are invisible** to a static parser.
   Flagged per file via `module_nodes.has_dynamic_imports`; graph reach is a
   lower bound.
4. **0.6% of TS/JS imports remain unresolved** (850 of 142,239). Mostly
   `products/desktop/`, a deliberately separate pnpm workspace.
5. **Review-comment → code-change linkage is a proxy.** Squash merging means
   intra-PR commit history is absent from `master`, so
   `followed_by_change_in_path` leans on thread resolution and the merged diff
   rather than on per-commit timing. It is evidence, not proof.
6. **`proximate` regression candidates are low precision by design.** They
   exist for recall and are marked `requires_human_confirmation`. Never treat
   one as a regression without checking it.

**Anomaly worth a human's attention:** one Git identity (`tom@posthog.com`)
authored 2,156 commits in 90 days — ~24/day. That is either an exceptionally
prolific engineer, a heavily AI-assisted workflow, or a shared/automation
account. It is *not* auto-classified as a bot; it surfaces through
`bot_classification_uncertain` and the anomaly flags so a human decides. Any
volume-based ranking would put this identity first, which is a good argument
for not building one.

**Deferred to Phase 2 (correctly):** semantic classification of review-comment
consequence; deciding what impact *means*; any weighting, scoring or ranking.

---

## 11. The contract Phase 2 must consume

**[`docs/PHASE_2_CONTRACT.md`](docs/PHASE_2_CONTRACT.md) — frozen at schema
version 1.0.0.** In short:

* Read `artifacts/run_manifest.json` first; everything else is under `artifacts/`.
* 16 normalized + 11 derived Parquet tables, each with a generated JSON Schema
  in `schemas/` and a content hash in the manifest.
* **Phase 1 contains no scores.** Filter leaderboards on
  `pull_requests.ranking_eligible`. Treat `NULL` as "not knowable", never 0.
  Bot authorship is a separate axis from eligibility. Candidates carry an
  evidence tier and the literal evidence string.
* Section 6 of the contract lists the PostHog-specific facts (Trunk merge
  queue, squash merges, AI co-authorship, distributed ownership) that a
  consumer will otherwise get wrong.

---

## 12. Git commit containing this phase

See `git log` on `main`; the validated run is committed with the message
`Phase 1 complete: validated 90-day PostHog extraction`.
