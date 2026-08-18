# PostHog Engineering Impact Dashboard — Phase 1

Reproducible data-ingestion foundation for an engineering-impact analysis of
[PostHog/posthog](https://github.com/PostHog/posthog).

This phase produces **evidence, not scores.** It extracts, normalizes and
cross-checks 90 days of repository history into a versioned, hash-verified
artifact package. Deciding what "impact" means — and defending that
definition — is Phase 2's job, and the data model is deliberately built so that
Phase 2 cannot quietly fall back on commit counts.

---

## Why this shape

Counting commits, lines or reviews does not measure impact, so nothing in this
pipeline emits a count as a verdict. Instead every pull request carries
*explainable evidence*:

| Instead of… | this phase records |
|---|---|
| "files changed" as a size proxy | components crossed, ownership boundaries crossed, import fan-in/out, downstream components, and a reachability band with its uncertainty |
| "wrote a feature" | the conventional prefix **and** whether the paths actually corroborate it |
| "left N reviews" | which comments were substantive vs nits/acks/bots, which named a safety concern, which were followed by a change, and the comment text itself |
| "caused a regression" | graded candidates — `explicit` (a revert names it) / `linked` (same issue or flag) / `proximate` (same files, needs a human) — never an accusation |
| "shipped a feature" | candidate episodes: sets of PRs linked by closing references, follow-up language and shared flags |

Every derived row carries the version of the code that produced it, and every
number traces back to a URL a reader can open.

---

## Architecture

```
                 ┌─ git clone (read-only, shallow-since) ─────────┐
                 │   commits · diffs · trailers · config snapshot │
  data/raw/  ◄───┤                                                │
   immutable     │   GitHub GraphQL: PRs · reviews · threads ·    │
                 └─   comments · issues · timeline ───────────────┘
       │                    (cached, resumable, rate-limit aware)
       ▼
  data/normalized/   16 entity tables — actors, pull_requests, pr_files,
       │             reviews, review_threads, references, components …
       ▼
  data/derived/      11 evidence tables — change shape, blast radius,
       │             episodes, regression candidates, review interventions,
       │             anomalies, dependency graph
       ▼
  artifacts/         Phase 2 package: Parquet + JSON Schema + run manifest
```

**Nine principles enforced in code**, not just documented: raw records are
immutable and retained; every derived field is version-stamped; extraction is
idempotent and resumable; counts are never emitted as scores; `NULL` ≠ `0`;
humans/bots/AI-assisted/generated are separable; window filtering is on
`mergedAt` with eligibility flagged; PostHog is never built or modified; no
secret reaches Git, data, logs or docs.

---

## Prerequisites

* Python **3.12+**
* `git` 2.40+
* A GitHub token with **read access to public repositories** — a fine-grained
  PAT scoped to *Public repositories (read-only)*, or an authenticated
  `gh` CLI. **No write scope is used anywhere.**
* ~4 GB free disk (1.4 GB clone + ~50 MB data)

## Setup

```bash
make deps                    # venv + pinned dependencies
cp .env.example .env         # set GITHUB_TOKEN, or just: gh auth login
```

## Run

```bash
make all                     # full pipeline, resumable — cached work is free
make status                  # how far it has got
make test                    # 125 unit + contract tests, no network needed
```

Individual stages:

```bash
make ingest-git              # clone/verify, commits + diffs          ~66 s
make ingest-github           # discovery, PR core, review detail      network-bound
make normalize               # raw -> normalized entity tables
make graph                   # module dependency graph                ~170 s
make features                # deterministic evidence features
make validate                # invariants, reconciliation, gates
make export                  # artifacts/ + run manifest
```

### Expected runtime and cost

| Stage | Time | API cost |
|---|---|---|
| `ingest-git` | ~1 min (plus ~4 min first clone) | none |
| `ingest-github` | ~45–75 min | ≈4,500 of 5,000 GraphQL points/hr |
| `graph` | ~3 min | none |
| `normalize` + `features` | ~2–4 min | none |
| `validate` + `export` | ~1 min | 1 request |

The GitHub stage is **latency-bound, not budget-bound**. GitHub answers a
25-PR batch in ~5 s regardless of fields requested, and its secondary limit
(90 CPU-seconds per 60 s wall) caps useful concurrency at ~2. `--workers 12`
produces continuous HTTP 403s and is *slower*. Total money cost: **$0.**

A rerun costs **zero** API budget — every request is cached by content hash.

## Widening the window

90 days is the default. The window is config and the schema does not change:

```bash
make window-year             # 365 days, widening the clone to match
# or
python -m impact all --window-start 2025-08-17T00:00:00Z
git -C data/raw/git/posthog fetch --unshallow   # full history
```

Request batches are bucketed by PR number rather than list position, so
widening the window re-fetches only the buckets that actually changed.

---

## Layout

```
config/       repository, window, component rules, bot rules, generated-file
              rules, feature versions + tunables
src/impact/
  ingest/     git extraction, GitHub client, discovery, run ledgers
  normalize/  title parsing, references, paths, components, actors
  features/   change shape, blast radius, episodes, regression, review
              interventions, anomalies
  graph/      Python AST + TS/JS lexical import parsing, module graph
  quality/    invariants, reconciliation, sampling, gates
schemas/      generated JSON Schema per output table
tests/        125 unit + contract tests (no network, no clone required)
docs/         data dictionary, runbook, repository mapping, quality report
artifacts/    the Phase 2 package
```

## Documentation

| Document | What it is for |
|---|---|
| [PHASE_1_HANDOVER.md](PHASE_1_HANDOVER.md) | Source SHA, window, measured results, API cost, known defects. |
| [CURRENT_STATE.md](CURRENT_STATE.md) | What works, what does not, the next command, riskiest assumptions. |
| [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | Every table and column, with null semantics. |
| [docs/INGESTION_RUNBOOK.md](docs/INGESTION_RUNBOOK.md) | Operating, resuming and debugging a run. |
| [docs/REPOSITORY_MAPPING.md](docs/REPOSITORY_MAPPING.md) | PostHog's conventions and how components/owners are derived. |
| [docs/QUALITY_REPORT.md](docs/QUALITY_REPORT.md) | Gate results and sample audits from the real run. |

---

## Four things about PostHog that change the analysis

Discovered empirically against the live repository; each would produce a wrong
dashboard if missed.

1. **A Trunk merge queue opens throwaway PRs.** Titles shaped
   `trunk-merge/pr-83501/<uuid>`, authored by `trunk-io`, draft, closed
   unmerged. They are flagged `is_merge_queue_artifact` and excluded from
   `ranking_eligible`. Counting them roughly doubles apparent output.
2. **Everything is squash-merged**, so one merged PR is exactly one commit on
   `master` — verified, not assumed. That is why file-level data comes from Git
   for free and the API budget goes to review data instead.
3. **AI co-authorship is widespread** (1,855 commits with an Anthropic
   co-author trailer in the window). Tracked as `has_ai_co_author`, kept
   strictly separate from bot classification — a human still opened and
   defended the PR.
4. **Ownership is distributed, not in CODEOWNERS.** 26 nested `owners.yaml`
   files carry the real mapping; `.github/CODEOWNERS` is deliberately tiny and
   argues against its own use. `CODEOWNERS-soft` does not exist.

## Licence note

`ee/**` is PostHog Enterprise-licensed, not MIT. It is tagged
`license_area` throughout and is never silently aggregated as open-source
contribution.
