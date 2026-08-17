# Phase 2 Handover — Explainable Impact Analytics

**Methodology version `1.0.0` · export schema `1.0.0` · consumes Phase 1 schema `1.0.0`**

> **Status.** Code complete; the export contract is **frozen** and
> [`docs/PHASE_3_CONTRACT.md`](docs/PHASE_3_CONTRACT.md) plus the committed
> fixture package at `docs/fixtures/phase3/` mean **Phase 3 can start now**.
> Sections marked **[MEASURED]** are real numbers from a completed stage against
> the live PostHog dataset. Sections marked **[PENDING RUN]** are filled in by
> `make p2`. Sections marked **[AWAITING HUMAN]** need a person and are
> deliberately not claimed as done — the export package carries
> `publishable: false` until they are.

---

## 1. What Phase 2 is

Phase 1 produced evidence and refused to score it. Phase 2 defines what impact
means, applies that definition transparently, and produces a static package
Phase 3 renders.

**The authoritative definition, implemented literally:** observable engineering
impact is a defensible change in product capability, user experience, system
quality, organizational leverage, or future delivery capacity that can be
materially attributed to an engineer's decisions and contributions using public
evidence.

**The unit of analysis is the impact episode**, not the commit and not the PR.
An episode is a connected initiative arc: motivating issue, implementation PRs,
consequential reviews, rollout/feature flags, follow-up corrections,
documentation, and downstream adoption.

**What is explicitly not used as ranking logic**, enforced by an adversarial
test each: commit count, PR count, lines of code, review count, velocity
ratios, per-day normalisation, and any composite 0–1000 score. There is no
score anywhere in the system.

---

## 2. Verified Phase 1 input manifest **[MEASURED]**

Phase 2 reads only `artifacts/`. Verification runs before any analysis
(`python -m impact2 verify-inputs`) and writes
`data/phase2/_input_verification.json`.

| | |
|---|---|
| Input source | `artifacts/` (the frozen Phase 1 contract surface) |
| Phase 1 schema version | `1.0.0` — asserted equal to `REQUIRED_PHASE1_SCHEMA` |
| Tables present | **27 / 27** |
| Rows loaded | **485,897** |
| File-hash failures | 0 |
| Verification status | `degraded` (see §2.1) |
| Analyzed HEAD SHA | `d4295d5794f95a0ae726edd0e27450115f3fc0a3` |
| Window | 90 days, half-open on `mergedAt`, UTC |

Row counts of the tables Phase 2 actually leans on:

| Table | Rows |
|---|---:|
| `pull_requests` | 19,320 |
| `pr_files` | 117,301 |
| `commits` | 15,485 |
| `dependency_edges` | 141,389 |
| `module_nodes` | 36,412 |
| `pr_change_shape` / `pr_blast_radius` / `pr_regression_candidates` | 15,291 each |
| `candidate_episode_edges` | 9,537 |
| `references` | 22,324 |
| `feature_flags` | 957 |
| `actors` | 610 |

`--verify-content-hashes` additionally re-hashes every table's rows and
compares against `run_manifest.json.tables.*.content_sha256`. It is off by
default because it costs a full read of every table; **run it before
publishing**.

### 2.1 Why the status is `degraded`, and what it disables **[MEASURED]**

Phase 1's GitHub *review-detail* pass had not completed when this analysis ran.
Four tables are empty, and Phase 2 records exactly what each one disables
rather than silently producing zeros:

| Empty table | Disabled |
|---|---|
| `review_comments` | review interventions, collaborative amplification |
| `review_threads` | review-thread resolution evidence |
| `review_intervention_candidates` | review causality, decision quality via review |
| `issues` | problem framing, originator role, linked-issue corroboration |

Consequences, all visible in the export:

* `collaborative_amplification` is band 0 or unknown for everyone. It must be
  rendered as *disabled*, not as "nobody collaborates".
* `decision_quality` loses its strongest evidence path (review-driven
  redesign) and falls back to PR-body rationale and simplification diffs.
* The `originator` role can only be inferred from first-PR problem framing.
* Episode problem statements fall back to PR bodies.

**This is the single highest-value thing to fix.** Finish `make ingest-github`,
re-run `make normalize features validate export`, then `make p2`. Nothing in
Phase 2 changes; the disabled capabilities light up and
`capabilities_disabled` empties.

---

## 3. Exact commands and environment

```bash
# One-time
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# Phase 2, end to end
make p2

# Or stage by stage — each is independently rerunnable
make p2-verify       # manifest + hashes
make p2-graph        # tiered artifact graph
make p2-episodes     # clustering + episode records
make p2-analytics    # propagation, decay, novelty, corrective, causality
make p2-attribute    # role-aware participants
make p2-dimensions   # six evidence-banded dimensions
make p2-portfolios   # ordered weighted aggregation
make p2-rank         # ELECTRE III across scenarios
make p2-llm          # OPTIONAL semantic layer
make p2-validate     # the ten-item validation programme
make p2-export       # the static Phase 3 package
```

Useful flags:

| Flag | Effect |
|---|---|
| `--verify-content-hashes` | re-hash every Phase 1 table (slow; do it before publishing) |
| `--allow-unexported` | read `data/normalized` + `data/derived` when `artifacts/` is absent. Marks the run PROVISIONAL and injects a blocking `known_gap`. Development only. |
| `--llm` | enable the semantic layer during `all` |
| `--replay` | serve every LLM task from cache; never call a provider |
| `--skip-sensitivity` | skip bootstrap + sensitivity (fast; export records stability as UNMEASURED) |

Environment: Python 3.12.1, macOS arm64. Dependencies unchanged from Phase 1 —
Phase 2 adds no new runtime dependency. **No account was created and no API key
is required.**

---

## 4. Episode construction **[MEASURED]**

### 4.1 The artifact graph — 68,872 edges

Every edge carries an evidence tier, the literal evidence string, and any guard
that demoted it.

| Tier | Meaning | Edges |
|---|---|---:|
| **A** deterministic | the data literally states the link | 19,228 |
| **B** structural | derivable from structure, no interpretation | 46,790 |
| **C** semantic | lexical similarity + temporal + component fit | 2,854 |

By type:

| Edge type | Count | Tier |
|---|---:|---|
| `symbol_downstream` | 35,652 | B |
| `shared_named_entity` | 4,911 | B |
| `semantic_similarity` | 2,854 | C |
| `direct_mention` | 2,186 | A |
| `changelog_link` | 2,116 | A |
| `depends_on` | 1,882 | B |
| `follow_up` | 1,476 | B |
| `shared_feature_flag` | 1,400 | A/B |
| `part_of` | 1,240 | B |
| `commit_membership` | 11,875 | A |
| `supersedes` | 781 | A |
| `reverts` | 649 | A |
| `stacked_branch` | 621 | B |
| `closes_issue` | 606 | A |
| `migration_dependency` | 581 | B |
| `reapplies` | 42 | A |

**25,123 edges carry a guard** (demoted or excluded). Guards are the difference
between a credible episode map and a blob:

* feature flags: 179 keys gave tier-A edges, 4 were demoted to tier B for
  fan-out > 8, 0 excluded;
* shared issues: 49 tier-A, 1 demoted for fan-out > 12;
* generic maintenance titles (`fix: flaky`, `chore(deps)`, …) cannot anchor a
  mention or follow-up join;
* `symbol_downstream` demotes above `hard_max` adopters — a helper adopted by
  forty PRs is a leverage signal, not evidence that forty PRs are one
  initiative.

### 4.2 Deviations from the phase spec's tier assignment, and why

The spec places "same feature-flag key" and "direct URL/number mention" in Tier
A. Both are implemented as Tier A **with a documented damping**:

* a plain `#1234` mention gets a type multiplier of **0.60** inside tier A. The
  number is literally in the text (so it is deterministic), but a mention is far
  weaker evidence of one initiative than a closing reference. Promoting them
  equally would merge unrelated work;
* a feature flag touched by more than 8 PRs is demoted to tier B, and above 40
  it is recorded but never clusters. On this repository flags like
  platform-wide kill switches are touched by dozens of unrelated PRs.

Both multipliers and both thresholds are config values in
`config/phase2/episodes.yaml` and are varied by the sensitivity analysis.

### 4.3 Clustering algorithm — `weighted_louvain@1.0.0`

1. Multiple edges between the same PR pair combine by **noisy-OR**,
   `1 - prod(1 - s_i)`, not by sum: two independent signals should raise
   confidence, two copies of the same bookkeeping should not.
2. Pairs below `min_pair_strength = 0.50` are dropped. A lone tier-C edge
   (0.35) is below this **by design** — a semantic edge cannot merge episodes
   by itself.
3. Structural-only joins spanning more than 45 days are dropped.
4. Deterministic weighted Louvain (hand-written, sorted node order, no RNG in
   the hot path) proposes communities.
5. Constraints dispose: clusters above 12 are re-clustered on tier A+B only;
   still above 25, they are split into connected components of tier A alone;
   anything that survives that is flagged `oversized_cluster` and queued rather
   than split arbitrarily.
6. Explicit `part_of` / `stacked_branch` / `depends_on` structure is preserved
   as `sub_episode_links` rather than flattened.

**Pair graph [MEASURED]:** 23,016 pairs kept, 26,003 dropped by guards and
thresholds. Every drop is in `data/phase2/cluster_audit_log.json` with its
reason.

**Cluster counts, audit actions, size distribution — [PENDING RUN]**
(`data/phase2/_run_summary.json → summaries.episodes`).

### 4.4 Cluster confidence and the review queue

Confidence is driven by the *kind* of evidence holding a cluster together, never
its size: tier-A share, whether all members are reachable by tier A/B evidence,
span, and member count. Clusters below 0.55 confidence, or larger than the split
threshold, go to `reports/phase2/audit_episode_clusters.json`.

### 4.5 Episode status, and merge ≠ release

Status is one of the seven the spec names. Because "merged to master" is not
"users saw it", `release_corroboration` is reported **separately** and takes
`corroborated` only on documentation/changelog evidence, feature-flag removal,
observed downstream adoption, or a linked issue closed as completed. Merging is
explicitly not in that list.

The rubric enforces the distinction rather than leaving it to the UI: **no
dimension reaches band 3 on an episode whose release is not corroborated.**

---

## 5. Dimension rubric

Six dimensions, ordinal bands 0–4, `null` = unknown ≠ 0. Rules live in English
in `config/phase2/rubric.yaml` and are executed by
`src/impact2/dimensions/rubric.py`.

Three invariants hold across all six:

1. **No band is inferred from volume.** Where a count appears it counts
   *distinct corroborating artifact classes* or *distinct downstream components
   and authors* — breadth of evidence, not amount of work. The collaboration
   evaluator additionally cannot see comment counts at all: its only input is
   causally-confirmed interventions.
2. **Band 3 requires ≥ 2 distinct artifact classes. Band 4 requires ≥ 3, an
   explicit textual marker, and high confidence.** A large diff in a risky
   directory cannot become a "platform-wide reliability triumph" on its own.
3. **Unknown is not zero.** No observable evidence → band 0 with a reason.
   Evidence that *could not be read* → `null`, which widens the interval and is
   excluded from pairwise comparison rather than counted as a weakness.

Additional caps, each recorded on the assessment:

* reachability `unknown` caps reliability at 2 (a high-blast-radius claim needs
  reach evidence);
* release not corroborated caps product outcome at 2;
* band 4 leverage requires observed persistence near the window end;
* a confirmed, un-reapplied revert caps durability at 1;
* band 4 reliability requires an *explicit* incident/security/postmortem
  marker in labels or text — the rubric never invents an incident from a `fix:`
  title.

Confidence falls one level per documented reason the evidence is thin (unknown
reachability, proximate-only corrective evidence, unmeasurable survival,
truncated threads, missing merge commit, low cluster confidence) and is used
twice: to discount criterion evidence, and to widen band intervals.

**Actual band distributions — [PENDING RUN]**
(`data/phase2/_run_summary.json → summaries.dimensions.band_distribution`).

---

## 6. Attribution rules

Nine roles, each with an evidence requirement that must be met before it is
assigned: originator, core implementer, contributing implementer, decision
shaper, risk preventer, integrator, rollout owner/sustainer, enabler/platform
author, documenter. There is no "was in the thread" role.

* PR author is **not** automatically originator or sole owner. Origination
  comes from issue authorship or first-PR problem framing; core implementation
  from production-code share (≥ 40%) or authoring the largest PR.
* Reviewer credit requires a **causally-confirmed consequential** intervention,
  not a comment.
* Git co-authors are retained as contributing implementers. AI co-author
  trailers are **excluded** from co-authorship credit and disclosed separately.

**Shared credit** is categorical — `primary` / `material` / `supporting` /
`unclear` — and the UI shows only the category. An interval exists internally
so the aggregation has something to multiply; it is never displayed as a
percentage.

**Double counting is prevented structurally.** The episode's outcome is scored
once. Attribution then decides how much of that evidentiary mass enters each
portfolio, per dimension, via
`factor = max over roles of (role_dimension_relevance x share_factor)`.
`max`, not sum — stacking roles cannot manufacture credit. The episode's total
factor per dimension is capped at 2.0 and proportionally scaled if exceeded, so
an episode can never be worth more than itself.

**Ambiguous cases and how they are handled:**

| Case | Handling |
|---|---|
| Several core implementers | all `material`, none `primary` |
| Reviewer with a high-confidence causal intervention and no code | `material` |
| Reviewer with a consequential concern but no confirmed change | `supporting` |
| Participant with no evidenced role | recorded, `unclear`, contributes nothing |
| Identity flagged ambiguous by Phase 1 | attribution confidence forced to `low` |
| Bot identity | excluded from attribution entirely, disclosed |

---

## 7. Deterministic analytics — formulas and configuration

| Analysis | Formula / rule | Key config |
|---|---|---|
| **A. Propagation** | time-respecting BFS from introduced/changed files through the import graph | `max_depth: 3`, `max_paths_per_episode: 400`, `max_edges_per_episode: 4000`, `max_frontier_per_depth: 150`, `max_recorded_edges_per_episode: 60` |
| Hub damping | `path_weight = 1 / (1 + log2(1 + fan_in))`, plus exclusion above the p99.5 fan-in cut (**= 76 on this repository [MEASURED]**) | `hub_fan_in_percentile_exclude: 99.5` |
| Mass cap | per-episode contribution capped; `cap_applied` reported | `max_propagation_mass_per_episode: 25.0` |
| **B. Decay** | `exp(-ln(2) * age_days / H)` | `half_life_days: 180` |
| Persistence override | `effective = max(raw_decay, survival_floor)` when ≥ 2 adoption events within 45 days of the window end | `survival_floor: 0.35` |
| **C. Novelty** | classes `new_capability` / `extension` / `simplification` / `maintenance_repeat`; `may_set_band_alone: false` | `distinctive_token_max_document_frequency: 0.02` |
| **D. Corrective burden** | five behavioural classes; only explicit/linked evidence carries a penalty; capped; applied to **one** dimension | `max_total_penalty: 1.0`, `applies_to_dimension: propagation_durability` |
| **E. Review causality** | high = change followed + acknowledged/resolved + consequential concern | `outdated_comment_is_change_evidence: true` |
| **F. Diversity** | entropy + concentration label, `affects_ranking: false` | — |
| **G. Uncertainty** | bootstrap over episodes, 400 resamples, seed 20260816 | — |

Raw age, decay factor and persistence are reported **separately** on every
episode and never multiplied into one opaque number.

**Propagation aggregates vs recorded edges.** The walk aggregates over *every*
adoption event but materialises a full edge record for only the first 60 of
them. Building a 17-key dict per event cost more than the rest of the pipeline
combined and told nobody anything the counters do not. Each episode therefore
reports both `adoption_events` (the true count, which the bands read) and
`edges_recorded` (the sample, which the UI shows as examples), so the sampling
is explicit rather than implied. `walk_truncated` is set when the event or
frontier cap binds, making the reach an honest lower bound.

### 7.1 Performance, and what it cost to get there **[MEASURED]**

Three fixes took the pipeline from "hours" to "minutes" on this dataset. All
three are algorithmic, none of them changes a band:

| Fix | Before | After |
|---|---|---|
| Memoised adopter expansion + path→component map (was rescanning each adopting PR's whole file list per edge) | propagation did not finish | — |
| Aggregate counters instead of a dict per adoption event | 13+ min | **12 s** for 9,515 episodes |
| Pair index for `cluster_confidence` / `sub_episode_links` (was scanning all ~31k pairs per episode ≈ 300M iterations) | episode build stalled | seconds |

Remaining costs, and the knobs that bound them:

* **TF-IDF semantic candidates ~2 min** over 13,318 PR documents. This is the
  single largest remaining cost and it produces edges that mostly cannot
  cluster anyway (68 of 2,992 were corroborated), so it is the first thing to
  cut if the run must be faster.
* **Stability analysis** is O(n²) in engineers per trial and is bounded by
  `sensitivity.candidate_pool_size` (30), `weight_perturbations` (80) and
  `bootstrap.resamples` (150). Raising any of them raises runtime
  superlinearly. `--skip-sensitivity` removes it entirely and the export
  records stability as UNMEASURED rather than pretending.

**On review causality and squash merges:** PostHog squash-merges, so a literal
pre-comment/post-comment revision diff is impossible on the analysed branch.
Phase 2 reports `pre_post_revision_compared: false` with that reason on every
intervention and leans on GitHub's `outdated` flag (the diff hunk moved after
the comment) plus thread resolution and author reply. It is evidence, not proof,
and says so.

---

## 8. Portfolio aggregation and outranking

### 8.1 Aggregation — the anti-volume mechanism

```
value_j = min(scale_max, v1 + min(headroom, sum(coeff_i * v_i for i >= 2)))
where v_i = band_i x confidence_discount_i x attribution_factor_i x decay_i
      coeffs = [1.00, 0.55, 0.30, 0.17, 0.10], headroom = 1.00, scale_max = 4.0
```

Episodes are ordered strongest-first. The strongest carries the evidentiary
mass; everything after is capped corroboration. **Ten band-1 episodes reach
2.0; one band-4 episode reaches 4.0** — a single transformative episode can
outrank many moderate ones, which is what the spec requires.

Episodes closing the same issue, or in the same propagation lineage, are not
independent and are collapsed to their strongest member once.

### 8.2 ELECTRE III

Standard concordance/discordance/credibility with Roy descending + ascending
distillation; ties in the intersected preorder become **tiers**, because some
engineers are genuinely incomparable on this evidence.

Balanced weights (normative starting preferences, configurable, exposed in the
UI, varied by sensitivity analysis): product 0.22, reliability 0.18, leverage
0.20, decision quality 0.14, durability 0.16, collaboration 0.10.
Thresholds start at `q=0, p=1, v=3`. Confidence discounts: high 1.00, medium
0.75, low 0.45.

**Unknown criteria are excluded from the pair and recorded** — never scored as
zero. This is visible in the export as `excluded_criteria` on every comparison.

**Counterevidence veto:** an engineer with a confirmed, un-reapplied revert of
an episode that reached band ≥ 3, at high confidence, cannot outrank another
solely via one criterion. Proximate regression candidates never trigger it —
Phase 1 marks them `requires_human_confirmation` and a veto is far too blunt for
an unconfirmed signal.

**PROMETHEE II** runs independently on the same inputs as a cross-check.
Disagreement is exported as `cross_check_delta`, not hidden.

### 8.3 Scenarios

| Scenario | Available |
|---|---|
| balanced, product, reliability, leverage emphasis | yes |
| high-confidence evidence only | yes |
| last 90 days | yes (identical to the full window; noted) |
| last 12 months | **no** — needs a 365-day window; remedy command exported |
| foundational / full history | **no** — needs a non-shallow clone; remedy exported |

Unavailable scenarios ship with `available: false`, the reason, and the exact
command that would make them available. Phase 3 renders them as disabled tabs.

### 8.4 Ranking results — [PENDING RUN]

Top five with evidence IDs, tiers, stability and pairwise explanations land in
`artifacts/phase3/rankings.json` and `comparisons.json`.

---

## 9. The optional LLM layer

**Not configured in this run.** No provider account was created, because doing
so requires human sign-in. The deterministic pipeline produced a complete
result without it, and every semantic task was written to
`data/phase2/LLM_PENDING.json` with instructions.

To enable it (entirely optional):

1. Open **one** of these and create a free account, then generate **one API key
   with no billing enabled**:
   * OpenRouter — <https://openrouter.ai> → Keys → `OPENROUTER_API_KEY`
   * Google AI Studio — <https://aistudio.google.com/app/apikey> → `GEMINI_API_KEY`
   * Groq — <https://console.groq.com/keys> → `GROQ_API_KEY`
2. Paste it into `.env` as `OPENROUTER_API_KEY=…` (or the provider equivalent).
   **Do not paste the value into a chat or a commit.** `.env` is git-ignored.
3. `make p2-llm && make p2-dimensions p2-rank p2-export`
4. Afterwards `LLM_REPLAY_ONLY=1 make p2` reproduces the run from cache with no
   key and no network.

Controls that are in place regardless: JSON-schema output, temperature 0,
prompt + model versioning, content-hash cache, retries with backoff, evidence
IDs required on every claim (uncitable IDs are dropped), author claims kept
separate from observed outcomes, identity blinding on comparisons, email and
token redaction before any payload leaves the process, and a per-run call
budget that queues rather than crashes.

**The deterministic band is always authoritative.** The LLM's band is stored
alongside it with an agreement flag; a disagreement is data, not an override.
Keyword classifiers are labelled `deterministic_rule` everywhere and are never
presented as semantic analysis.

* Provider / model / prompt versions / cache location / token totals /
  repeatability — **[PENDING RUN]**, `data/phase2/llm_report.json`. **Never
  keys.**

---

## 10. Validation programme

| # | Item | Status |
|---|---|---|
| 1 | Episode clustering audit, ≥ 30 stratified clusters | **[AWAITING HUMAN]** — queue generated |
| 2 | Rubric agreement, ≥ 25 episodes, two passes, kappa + alpha | **[PENDING RUN]** |
| 3 | LLM stability, ≥ 20 cases | pending — no provider configured |
| 4 | Review causality, ≥ 15 candidates incl. false positives | **skipped** — no review data yet (§2.1) |
| 5 | Regression links, ≥ 15 candidates | **[PENDING RUN]** |
| 6 | Ranking sensitivity | **[PENDING RUN]** |
| 7 | Adversarial tests | **[PENDING RUN]** |
| 8 | Reproducibility | **[PENDING RUN]** |
| 9 | Claim audit — zero orphans | **[PENDING RUN]** |
| 10 | Human approval of all five finalists | **[AWAITING HUMAN]** — ledger generated |

**Item 2's honesty note, recorded in the output itself:** both passes are
machine passes (the second is a rule variant with relaxed corroboration and
shifted thresholds). It measures how sensitive the bands are to the thresholds
*we chose*, not human inter-rater agreement. A blind human queue for real kappa
is emitted at `reports/phase2/audit_rubric_blind_review.json` and is unfilled.

**Item 7's five attacks**, each targeting a named failure mode: episode
splitting (breaks PR counters), a 40,000-line generated migration (breaks line
counters), 100 trivial approvals (breaks review counters), all count columns
nulled (catches a count leaking into the evidence path), and a bot with a full
portfolio (must be excluded, not merely ranked low). Pass criteria are Kendall
tau ≥ 0.90 and top-five overlap = 1.0.

Human queues live in `reports/phase2/`:
`audit_episode_clusters.json`, `audit_rubric_blind_review.json`,
`audit_review_causality.json`, `audit_regression_links.json`,
`audit_finalist_approvals.json`. Re-running validation **preserves verdicts
already recorded** in them.

---

## 11. Output schemas, paths and hashes

### Internal (Phase 2's own working tables) — `data/phase2/`

`artifact_edges`, `artifact_nodes`, `impact_episodes`, `episode_artifacts`,
`episode_participants`, `episode_dimensions`, `propagation_edges`,
`propagation_summary`, `episode_novelty`, `episode_corrective_burden`,
`review_interventions`, `engineer_portfolios` (Parquet, each with a
`.meta.json` sidecar carrying row count, content hash and sort keys), plus
`cluster_audit_log.json`, `episode_review_queue.json`, `ranking_runs.json`,
`scenarios.json`, `sensitivity.json`, `claims.json`, `LLM_PENDING.json`.

### The contract — `artifacts/phase3/`

`dashboard_manifest.json`, `rankings.json`, `engineers.json`, `episodes.json`,
`evidence.json` + `evidence/<kind>.json`, `comparisons.json`, `claims.json`,
`methodology.json`, `coverage.json`, `indexes.json`.

Full field-by-field specification: **[`docs/PHASE_3_CONTRACT.md`](docs/PHASE_3_CONTRACT.md)**.
Sample records: **`docs/fixtures/phase3/`** (synthetic, committed, complete).

File hashes and byte sizes — **[PENDING RUN]**
(`dashboard_manifest.json.files`).

---

## 12. Known defects, limitations and deferred questions

**Phase 1 defects found and fixed during Phase 2** (both blocked the contract):

1. `src/impact/quality/report.py` did not exist, so `make validate` raised
   `ImportError` and `artifacts/quality_report.json`,
   `run_manifest.json.quality_gates` and `known_gaps` — which the contract says
   Phase 2 *must* read — could never be produced. Implemented.
2. There was no `Makefile`, though the Phase 1 handover documents `make all`.
   Added, covering both phases.

**Structural limitations, all disclosed in the export:**

1. **Review data missing in this run** (§2.1) — the largest gap.
2. **90-day window.** Foundational work predating it is invisible; work in
   flight at either boundary is truncated. Two scenarios are unavailable
   because of it, with remedies exported.
3. **Shallow clone.** "New" means new to the observed history, not new to the
   repository. Attached to every novelty record.
4. **Rust / Go / SQL / Hog / Ruby imports are not parsed.** Leverage and
   durability are `unknown` — never `low` — for work confined to them.
5. **Review causality is inferred, not proven** (squash merges, §7).
6. **Lexical similarity is not semantic understanding.** Tier C is labelled
   `tfidf_cosine` and cannot merge episodes without corroboration.
7. **Propagation walks are truncated** at 4,000 edges per episode;
   `walk_truncated` is set so a truncated reach never reads as a complete one.
8. **Attribution intervals are estimates.** Where evidence does not settle it,
   the category is `unclear` rather than a guess.

**Deferred questions for a future phase:**

* `data/raw/web/` and `web_artifacts.parquet` (108 rows) now exist from a Phase
  1 `ingest-web` stage added after the contract froze. Fetched docs/changelog
  pages would be a *much* stronger release-corroboration signal than
  "a docs file changed". Phase 2 does not read them; wiring them in is a
  contract addition, not a code rewrite.
* Human-approved semantic episode edges: the plumbing exists
  (`promotes_to_corroborated`) but there is no approval UI.
* Cross-repository propagation (posthog-js, posthog-python) is out of scope and
  makes leverage a lower bound.

---

## 13. Exact instructions for Phase 3

1. **Read [`docs/PHASE_3_CONTRACT.md`](docs/PHASE_3_CONTRACT.md) first.** It is
   frozen at export schema `1.0.0`.
2. **Build against `docs/fixtures/phase3/` today.** It is complete, tiny and
   deliberately awkward — it contains an unrankable engineer, `null` dimension
   values, a `merged_only` episode, unconfirmed counterevidence, an excluded
   criterion, a shared tier with mutual incomparability, and an unavailable
   scenario. Point the app at `artifacts/phase3/` in production; **no code
   change should be required to switch.**
3. **Render only claims.** Every sentence comes from `claims.json` via a
   `*_claim_id`. A string that is not a claim lookup is a contract violation.
   Every rendered claim must expose its evidence URLs and its `claim_id`.
4. **Respect `publishable`.** Do not deploy to a public URL while it is
   `false`; show the blockers banner instead.
5. **`null` is never `0`.** Break the axis, grey the spoke, show the
   `unknown_reason`. Do not sort unknowns as worst.
6. **Show `release_corroboration` next to `status`.** `shipped_observable` +
   `merged_only` must read as "merged; release not independently corroborated".
7. **Show tiers, not a podium.** Same tier = not distinguishable. Surface
   `incomparable_with`.
8. **Never** render a composite score, a shared-credit percentage, or any
   sentence about a person's ability, effort, seniority or productivity. The
   subject of every sentence is the work.
9. Call the product **observable repository impact** — never "top engineers",
   never "productivity".
10. Display the window, the analyzed HEAD SHA and the limitations headline on
    the landing page.

---

## 14. Next executable command

```bash
# 1. Finish the review-detail extraction — the largest quality win available
make ingest-github && make normalize features validate export

# 2. Re-run Phase 2 end to end with full input verification
./.venv/bin/python -m impact2 all --verify-content-hashes

# 3. Work the human queues
open reports/phase2/audit_episode_clusters.json      # >= 30 verdicts
open reports/phase2/audit_review_causality.json      # >= 15 verdicts
open reports/phase2/audit_regression_links.json      # >= 15 verdicts
open reports/phase2/audit_finalist_approvals.json    # all 5 finalists

# 4. Re-run validation + export; `publishable` flips to true when they are done
make p2-validate p2-export
```

---

## 15. Git commit containing this phase

**[PENDING RUN]** — recorded after the validated run is committed.
