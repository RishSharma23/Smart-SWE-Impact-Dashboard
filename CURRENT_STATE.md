# Current State — for a fresh coding agent

Read this first, then:

* [`PHASE_1_HANDOVER.md`](PHASE_1_HANDOVER.md) — ingestion detail
* [`docs/PHASE_2_CONTRACT.md`](docs/PHASE_2_CONTRACT.md) — what Phase 2 reads
* [`PHASE_2_HANDOVER.md`](PHASE_2_HANDOVER.md) — the analytics phase
* [`docs/PHASE_3_CONTRACT.md`](docs/PHASE_3_CONTRACT.md) — **what the UI reads**

---

## Phase 3 can start now

The export contract is **frozen** at export schema `1.0.0` and a complete
synthetic fixture package is committed:

```
docs/fixtures/phase3/      # 15 files, ~127 KB, 106 claims — build against this
artifacts/phase3/          # the real package, produced by `make p2-export`
```

The fixture deliberately contains every case that is easy to render wrongly: an
unrankable engineer, `null` dimension values, an episode that merged without
corroborated release, unconfirmed counterevidence, an excluded criterion, a
shared tier with mutual incomparability, an unavailable scenario, and
`publishable: false`. Point the app at the fixture directory in development and
at `artifacts/phase3/` in production — **no code change should be needed to
switch.**

---

## What works, verified against the live PostHog dataset

**Phase 1** — clone + Git extraction (66 s, 15,485 commits), GitHub discovery
(23,855 PRs), dependency graph (36,412 nodes / 141,389 edges, 99.4% import
resolution), normalize, features, validate, export. `artifacts/` holds all 27
contract tables and `run_manifest.json`.

**Phase 2** — verified end to end against those artifacts:

* input verification: 27/27 tables, 485,897 rows, 0 hash failures;
* artifact graph: **68,872 edges** — 19,228 tier A, 46,790 tier B, 2,854 tier C;
  25,123 carry a demotion guard;
* pair graph: 23,016 pairs kept, 26,003 dropped by guards/thresholds;
* clustering: **9,762 episodes** (8,690 single-PR, largest 24, median 1);
* propagation, novelty, corrective burden, six-dimension rubric, role-aware
  attribution, ELECTRE III + PROMETHEE II across 8 scenarios, bootstrap and
  sensitivity analysis, claim registry, 10-item validation programme, export.

**Two Phase 1 defects found and fixed** (both blocked the contract):
`src/impact/quality/report.py` did not exist, so `make validate` raised
`ImportError` and `quality_report.json` / `known_gaps` could never be produced;
and there was no `Makefile` despite the handover documenting `make all`.

## What is not complete

1. **Phase 1's review-detail pass has not finished.** `review_comments`,
   `review_threads`, `review_intervention_candidates` and `issues` are empty.
   Phase 2 records this as `capabilities_disabled` and degrades honestly, but
   it means collaborative amplification is unknown for everyone and decision
   quality has lost its strongest evidence path. **This is the highest-value
   thing to fix.**
2. **Four validation items await a human.** Cluster audit, review-causality
   verification, regression-link verification and finalist approval. The export
   carries `publishable: false` until they are recorded — by design.
3. **The LLM semantic layer is not configured.** No account was created (it
   needs human sign-in). The deterministic pipeline produced a complete result
   and every semantic task is queued in `data/phase2/LLM_PENDING.json` with
   instructions. It is optional.
4. **No Phase 1 or Phase 2 commit exists yet.**

## The next command to run

The full rebuild is **already queued** — it waits for the ingest to finish, then
runs the Phase 1 tail and all of Phase 2 by itself:

```bash
make p2-queue          # queue it (safe to run again; it takes a lock)
make p2-queue-status   # where it got to
tail -f reports/phase2/auto_run.log
```

The queue runs: wait for `impact ingest-github` to exit → check the extraction
ledger reported a clean finish (and warn loudly if not) → `normalize`,
`features`, `validate`, `export` → `impact2 all --verify-content-hashes`.
A failed `validate` gate does **not** stop it: the failure is recorded and
propagates into the Phase 2 export as a known gap, which is the honest
behaviour.

Afterwards, the only remaining work is human:

```bash
ls reports/phase2/audit_*.json     # 4 queues; verdicts are preserved on re-run
make p2-validate p2-export         # `publishable` flips to true when they are done
```

`make status` shows how far each stage has got.

## Runtime

Phase 2 end to end is a few minutes on an M1 Air. The two costs worth knowing:

* **Semantic candidate generation** (~2 min) — TF-IDF over 12,090 PR documents.
* **Stability analysis** — the outranking model is O(n²) in engineers and is
  re-run per trial. It is bounded by `sensitivity.candidate_pool_size` (30),
  `weight_perturbations` (80) and `bootstrap.resamples` (150). Raising any of
  them raises runtime superlinearly. `--skip-sensitivity` skips it entirely and
  the export records stability as UNMEASURED.

Two propagation caps exist purely for tractability and are reported per episode
rather than hidden: `max_edges_per_episode` (4,000, sets `walk_truncated`) and
`max_frontier_per_depth` (150).

## Highest-risk assumptions

1. **`ranking_eligible` is the right filter.** It excludes ~2,000 Trunk
   merge-queue artifacts. Counting them roughly doubles everyone's apparent
   output — the single easiest way to produce a wrong dashboard.
2. **Episodes, not PRs, are the unit.** If clustering is wrong, everything
   downstream is wrong. That is why 30 clusters go to a human queue and why
   every merge/split decision is in `cluster_audit_log.json`.
3. **Merge is not release.** `status: shipped_observable` +
   `release_corroboration: merged_only` must read as "merged; release not
   independently corroborated". The rubric enforces it (no band 3 without
   corroboration); the UI must not undo it.
4. **`null` is not `0`.** An unknown dimension is excluded from pairwise
   comparison and widens the interval. Rendering it as a zero bar would invert
   the meaning.
5. **Review causality is inferred, not proven.** Squash merges remove intra-PR
   history; the evidence is comment ordering, thread resolution and GitHub's
   `outdated` flag.
6. **Lexical similarity is not semantic understanding.** Tier C edges are
   labelled `tfidf_cosine` and cannot merge episodes without corroboration —
   only 42 of 2,854 candidates were corroborated.
7. **Static import parsing is a lower bound.** Rust/Go/SQL/Hog/Ruby are not
   parsed; leverage and durability for work confined to them are `unknown`,
   never `low`.

---

# PHASE 3 — UI, integration and hosting

_Updated 17 August 2026._

## Deployment status: LIVE

**<https://rishsharma23.github.io/Smart-SWE-Impact-Dashboard/>**

Built from the real Phase 2 export (9,515 episodes, generated
2026-08-17T07:20:04Z), 608 pages, deployed to GitHub Pages from the `gh-pages`
branch. Verified from a clean browser session: all six routes 200, 404 correct,
zero console errors, zero failed requests. CI run 32043919057 green on both jobs.

**Next command** — start the auto-refresh watcher so the site follows Phase 2:

```bash
nohup scripts/watch-phase2.sh > /tmp/phase3-watch.log 2>&1 &
```

To redeploy by hand:

```bash
scripts/deploy-dashboard.sh "Reviewed and approved by <name> on <date>."
```

Full detail in `PHASE_3_HANDOVER.md`.

## Blockers

None for Phase 3. One thing to be aware of:

1. **The original `main` history cannot be pushed.** Its history contains
   `web/node_modules/@next/swc-darwin-arm64/next-swc.darwin-arm64.node` at
   **124 MB**, over GitHub's 100 MB hard limit, plus `data/phase2/claims.json`
   (99 MB) and `artifacts/phase3/{episodes,claims}.json` (86/81 MB). Phase 3's
   source therefore went onto the orphan branch **`main-clean`**, which was
   pushed as remote `main`. Your original history is intact locally on `main` and
   `backup/pre-cleanup` (`b2b4dd86`); a `filter-branch` recipe to salvage it is
   in `PHASE_3_HANDOVER.md` §1. Nothing needs doing unless you want that history
   on the remote.

## Highest-risk assumptions

1. **A recorded human approval is standing in for Phase 2's own gate.** The
   export says `publishable: false`, `validation_status: fail`,
   `safety_scan: fail`. The owner reviewed and authorised publication; that is
   recorded as build metadata and the manifest is untouched, with both the
   approval and Phase 2's verdict shown on the site. If the approval was given
   without reading §6 of the handover, re-read it — the reasoning is that all 20
   safety findings are demonstrable false positives.

2. **The safety scanner cannot pass as written.** It reads `pkg@semver` as an
   email address and flags `methodology.json`'s own `forbidden_inferences` list.
   Until fixed, every run needs a manual override, which erodes the value of the
   gate.

3. **Rank stability is missing from this export** (`--skip-sensitivity`). The UI
   says so rather than implying certainty, but the top five are currently
   published without any position-range evidence. A full re-export fixes it with
   no UI change.

4. **250 of 9,515 episodes have dedicated pages.** Chosen by top-five relevance.
   Correct for the deliverable's scope; wrong if someone expects every episode to
   be individually addressable.

## Phase 2 defects to fix (found while integrating)

| # | Defect | Where documented |
|---|---|---|
| 1 | `claim.confidence` carries `corroborated`/`merged_only`, not the documented `high\|medium\|low\|null` | `docs/DATA_INTEGRATION.md` |
| 2 | `interval` is `[null, null]` where the contract says `[number, number]` | `docs/DATA_INTEGRATION.md` |
| 3 | 5 episode participants reference `git/email/*` actors absent from `engineers.json` | surfaced on the site's coverage page |
| 4 | Safety scanner false positives (3 classes) block `publishable` permanently | `PHASE_3_HANDOVER.md` §6 |
| 5 | Recommended: prune the export to what the UI renders (190 MB → ~10 MB) so CI can build production | `PHASE_3_HANDOVER.md` §11 |

## What was verified

- Typecheck clean; 31/31 component tests; 25 Playwright tests pass (3 conditional skips)
- Production build against the real export: 608 pages, 282 MB, ~105 s
- No console errors, page errors or hydration warnings on any route with real data
- Top five names, order and tiers checked by hand against `rankings.json`
- Every package file's sha256 verified against `dashboard_manifest.json`
- No horizontal scroll at 360 px on any top-level route

## Not verified

Lighthouse/Core Web Vitals (needs the live URL), accessibility audit
(deliberately deferred — see `docs/ACCESSIBILITY.md`), Firefox/WebKit,
visual regression, and link checking at scale.
