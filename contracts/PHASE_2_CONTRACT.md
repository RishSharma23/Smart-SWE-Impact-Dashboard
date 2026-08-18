# Phase 2 Consumption Contract

**Status: FROZEN at schema version `1.0.0`.** Phase 2 may be written against
this document. Any change to a column name, type, or meaning below requires a
`SCHEMA_VERSION` bump in `src/impact/versions.py` and an entry in the
[Changelog](#changelog).

---

## 1. What Phase 2 reads

Everything, and only, from `artifacts/`. Nothing under `data/` is contract.

```
artifacts/
  run_manifest.json                  <- read this FIRST (see §2)
  <table>.parquet                    <- 27 tables, §4 and §5
  quality_report.json                <- gate results, known gaps
  graph_coverage.json                <- import-parser coverage
  feature_summary.json               <- aggregate feature statistics
  component_rules_snapshot.json      <- ownership rules + their hashes
schemas/
  <table>.schema.json                <- JSON Schema per table, generated
data/samples/
  <table>.sample.json                <- 25-row fixtures, committed, for tests
```

Load with anything that reads Parquet. DuckDB is the intended path:

```sql
SELECT * FROM read_parquet('artifacts/pull_requests.parquet');
```

---

## 2. Read `run_manifest.json` first

It pins what the data *is*, and Phase 2 must surface these in any UI that
shows numbers:

| Field | Meaning |
|---|---|
| `source.analyzed_head_sha` | exact commit the repository snapshot was taken at |
| `source.clone_strategy` / `is_shallow_clone` | how much history was available |
| `window.start` / `window.end` | UTC analysis window (inclusive start, exclusive end) |
| `versions.*` | pipeline / extractor / schema / per-feature versions |
| `tables.<name>.row_count` | expected rows; a mismatch means a stale artifact |
| `tables.<name>.content_sha256` | order- and writer-independent content hash |
| `quality_status` / `quality_gates` | pass/fail per gate |
| `known_gaps` | **must be read**; see §7 |
| `coverage.completeness_by_month` | extraction completeness per month |

---

## 3. Five rules that are not negotiable

**3.1 — Nothing in Phase 1 is a score.** There is no impact number, weight, or
ranking anywhere in these tables. Every column is either a raw fact or a
counted/flagged piece of evidence. Defining impact is Phase 2's job, and any
weighting Phase 2 applies must be justified and shown to the user.

**3.2 — Filter on `ranking_eligible` for any leaderboard.**
`pull_requests.ranking_eligible` is true when the PR is `MERGED`, merged inside
the window, and is not a merge-queue artifact. Everything else stays in the
dataset with `ranking_ineligible_reason` set. Do not invent your own filter
before reading §6.

**3.3 — Bot authorship is a separate axis from eligibility.** A bot-authored PR
can be `ranking_eligible`. Use `author_is_bot` / `author_bot_probability` to
decide, and say what you decided. Filtering bots silently is the failure mode
this pipeline exists to prevent.

**3.4 — `NULL` ≠ `0` ≠ `false`.** A null means *not recorded* or *not
knowable*. `survival_30d = NULL` means the window ended before day 30, not
"nothing survived". `additions = NULL` with
`line_counts_unavailable_reason = 'binary_file'` means the file is binary, not
that it changed zero lines. Aggregations must exclude nulls explicitly, not
coalesce them to zero.

**3.5 — Candidates are candidates.** `pr_regression_candidates`,
`candidate_episode_edges` and `review_intervention_candidates` carry a
`strength` / `evidence_tier` and the literal evidence string. A `proximate`
regression candidate has *not* been shown to be a regression;
`requires_human_confirmation` marks exactly those.

---

## 4. Normalized tables

Join keys are in **bold**. All timestamps are ISO-8601 UTC strings ending `Z`.

### `actors` — one row per identity
**`actor_id`** (`github/user/<login>` or `git/email/<hash>`) · `login` ·
`display_name` · `account_type` (`user`|`bot`|`git_identity`) ·
`bot_probability` (0.0–1.0) · `bot_reasons[]` · `is_bot` (probability ≥ 0.9) ·
`is_ai_assistant_identity` · `emails[]` (only addresses already public in Git
history) · `git_names[]` · `identity_cluster_id` · `identity_cluster_size` ·
`identity_cluster_members[]` · `ambiguity_status` (`resolved`|`ambiguous`) ·
`ambiguity_reasons[]`

> Cluster identities before aggregating per person. One human commonly appears
> as a GitHub login plus one or more Git email identities; group on
> `identity_cluster_id`, not `actor_id`, for per-person totals.

### `pull_requests` — one row per PR (the spine)
**`pr_number`** · `pr_id` · `url` · `title_raw` · `body_text` · `state` ·
`is_draft` · `created_at` · `updated_at` · `closed_at` · `merged_at` ·
**`author_actor_id`** · `author_login` · `author_is_bot` ·
`merged_by_actor_id` · `merge_commit_sha` · `labels[]` · `assignee_logins[]` ·
`participant_logins[]`

*Parsed title:* `title_prefix` (normalized: feat/fix/chore/…) ·
`title_prefix_raw` · `title_scope` · `title_breaking` · `title_subject` ·
`title_parser_status` · `title_parser_confidence` (0.0–1.0) · `title_class`

*Size descriptors (never scores):* `github_additions` · `github_deletions` ·
`github_changed_files` (GitHub-reported) · `git_additions` · `git_deletions` ·
`git_file_count` (computed from the merge commit) · `has_binary_files` ·
`is_bulk_change` · `bulk_category`

*Counts:* `review_count` · `review_thread_count` · `comment_count` ·
`commit_count` · `reaction_count`

*Cohort / eligibility:* `cohorts[]` · `merged_in_window` · `created_in_window` ·
`context_only` · `is_merge_queue_artifact` · **`ranking_eligible`** ·
`ranking_ineligible_reason` · `has_merge_commit_in_clone`

### `pr_files` — one row per (PR, file)
**`pr_number`** · **`path`** · `pr_file_id` · `commit_sha` · `old_path` ·
`new_path` · `change_status` (`A`|`M`|`D`|`R`|`C`|`T`) · `similarity_score` ·
`additions` · `deletions` · `is_binary` · `line_counts_unavailable_reason` ·
`language` · `categories[]` · `risk_surfaces[]` · boolean shorthands
(`is_test`, `is_docs`, `is_generated`, `is_snapshot`, `is_lockfile`,
`is_vendor`, `is_migration`, `is_config`, `is_binary_asset`, `is_styling`,
`is_localization`, `is_ci`) · **`component`** · `platform` ·
`component_source` · `component_rule_priority` (1–6) · `component_rule_pattern` ·
`owners[]` · `owner_source` · `license_area` · `mapping_uncertainty[]`

> Sourced from Git, not the API: PostHog squash-merges, so one merged PR is
> exactly one commit on `master` and its diff is authoritative and complete.

### `commits` / `commit_parents`
**`commit_sha`** · `parent_shas[]` · `author_name` · `author_email` ·
`author_actor_id` · `authored_at` · `committer_*` · `subject` · `message` ·
`co_authors[]` · `co_author_actor_ids[]` · `has_ai_co_author` · `trailers{}` ·
`is_revert` · `revert_of_subject` · `is_cherry_pick` · `pr_number` ·
`pr_mapping_source` · `pr_number_from_subject` · `in_window`

> `pr_number` comes from GitHub's `mergeCommit.oid`, never from the `(#123)`
> suffix. `pr_number_from_subject` is the suffix, retained only so the two can
> be reconciled.

### `reviews` / `review_threads` / `review_comments`
`reviews`: **`review_id`** · **`pr_number`** · `reviewer_actor_id` · `state`
(`APPROVED`|`CHANGES_REQUESTED`|`COMMENTED`|`DISMISSED`) · `submitted_at` ·
`body_text`

`review_threads`: **`thread_id`** · **`pr_number`** · `path` · `line` ·
`diff_side` · `is_resolved` · `is_outdated` · `resolved_by_login` ·
`comment_count` · `comments_truncated` · `component` · `owners[]` ·
`participant_logins[]`

`review_comments`: **`comment_id`** · **`thread_id`** · **`pr_number`** ·
`position_in_thread` · `is_thread_opener` · `author_actor_id` · `body_text` ·
`created_at` · `reply_to_id` · `path` · `url`

### `issues` / `comments` / `references` / `feature_flags`
`issues`: **`issue_number`** · `state` · `state_reason` · `labels[]` ·
`author_actor_id` · timestamps

`comments`: **`comment_id`** · `parent_kind` (`pull_request`|`issue`) ·
**`parent_number`** · `author_actor_id` · `body_text`

`references`: `source_kind` · **`source_number`** · `reference_kind`
(`issue_or_pr`|`url`|`feature_flag`|`edge_phrase`|`external_artifact`) ·
`reference_value` · `reference_subtype` · `strength`
(`strong`|`medium`|`weak`) · `source_field` · `evidence`

`feature_flags`: **`pr_number`** · **`flag_key`** · `detection` · `diff_side` ·
`strength` · `evidence` · `owner_annotation` (the `// owner: #team-x` note from
PostHog's flag registry)

### `components` / `path_map`
`components`: **`component`** · `platform` · `label` · `source_rule` ·
`path_glob` · `manifest_path` · `manifest_sha256` · `snapshot_commit`

`path_map`: **`path`** · `component` · `owners[]` · `component_rule_priority` ·
`license_area` · `uncertainty[]` · `nearest_agents_file`

### `web_artifacts` — public corroboration
**`url`** · `host` · `reference_subtype` (`docs`|`changelog`|`handbook`|
`tutorial`|`roadmap`) · `referenced_by[]` (`pull_request/1234`, `issue/56`) ·
`reference_count` · `title` · `description` · `http_status` · `final_url` ·
`content_sha256` · `content_bytes` · `retrieved_at` · **`extraction_status`**
(`ok`|`http_error`|`blocked_by_robots`|`fetch_failed`|`skipped_cap`|
`skipped_offline`) · `error`

> Only URLs an in-window PR or issue explicitly linked to, and only pages that
> corroborate a shipped change. Every host's `robots.txt` is fetched once and
> honoured; a page we chose not to fetch is recorded with its reason rather
> than omitted, so "disallowed" and "does not exist" stay distinguishable.

### `raw_pages` / `extraction_runs` — provenance
Every API request issued (request hash, cursor, response path + hash, status,
rate-limit cost) and every stage invocation. Use these to prove a number came
from somewhere.

---

## 5. Derived evidence tables

### `pr_change_shape` — **`pr_number`**
`dominant_component` · `dominant_component_share` · `distinct_components` ·
`component_entropy` · `component_histogram{}` · `category_histogram{}` ·
`files_product_code` / `files_tests` / `files_docs` / `files_migration` /
`files_generated` / … · `files_added|modified|deleted|renamed|copied` ·
`code_file_count` · `code_share` · `test_to_production_links[]` ·
`test_to_production_link_count` · `production_without_test_change` ·
`touches_public_api` · `touches_schema` · `touches_migration` ·
`touches_auth_privacy` · `touches_billing` · `touches_ingestion` ·
`touches_data_pipeline` · `touches_deployment` · `touches_shared_library` ·
`touches_feature_flag_surface` · `touches_enterprise_licensed_code` ·
`primary_language` · **`title_claim_corroborated`** · `title_claim_note`

> `title_claim_corroborated` compares the conventional prefix against what the
> paths actually show. `false` means the claim and the paths disagree — a fact
> about the PR, not a verdict on the author. `NULL` means the prefix makes no
> checkable claim (e.g. `chore`).

### `pr_blast_radius` — **`pr_number`**
Eight independent signals plus a band:
`distinct_components` · `distinct_products` · `crosses_component_boundary` ·
`distinct_owners` · `crosses_ownership_boundary` · `files_without_owner` ·
`changed_fan_in_total` · `changed_fan_in_max` · `changed_fan_out_total` ·
`hub_files_touched` · `graph_coverage_share` · `downstream_file_count` ·
`downstream_component_count` · `downstream_components[]` ·
`downstream_product_count` · `touches_shared_library` ·
**`reachability_band`** (`local`|`component`|`cross_product`|`platform_wide`|
`unknown`) · `reachability_uncertainty[]` · `reachability_is_uncertain`

> `unknown` is a real answer, not a default. Rust/Go/SQL changes have no parsed
> imports; presenting them as `local` would be false.

### `candidate_episode_edges` — **`source_pr_number`**, `edge_type`, `target_number`
`edge_type` ∈ `closes_issue` · `references_pr` · `follow_up` · `part_of` ·
`stacked_on` · `reverts` · `reapplies` · `supersedes` · `shared_issue` ·
`shared_feature_flag` · `timeline_connected`
plus `target_kind` · `target_pr_in_dataset` · `strength` · `evidence_source` ·
`evidence`

### `candidate_episodes` — **`episode_id`**
`pr_numbers[]` · `pr_count` · `edge_types[]` · `root_pr_number`.
Connected components over **medium+** PR↔PR edges only; weak edges (shared
flag) are recorded but never group.

### `pr_regression_candidates` — **`pr_number`**
`explicit_regression_signals[]` · `linked_fix_candidates[]` ·
`proximate_fix_candidates[]` · **`regression_evidence_tier`**
(`explicit`|`linked`|`proximate`|`none`) · `requires_human_confirmation` ·
`was_reverted` · `corrective_churn{self_follow_up, collaborator_follow_up,
revert, replacement, unknown}` · `files_introduced` · `survival_30d` ·
`survival_60d` · `survival_90d` (+ `_reason` when NULL) ·
`tests_added_with_fix`

### `review_intervention_candidates` — **`candidate_id`**
`pr_number` · `comment_id` · `thread_id` · `url` · `commenter_actor_id` ·
`pr_author_actor_id` · `is_self_comment` · `path` · `component` ·
**`substance_class`** (`substantive`|`nit`|`acknowledgement`|`short`|`bot`|
`empty`) · `is_substantive` · `substance_reasons[]` ·
`safety_categories[]` · `safety_terms_matched{}` · `has_safety_vocabulary` ·
`thread_is_resolved` · `thread_resolved_by_author` ·
`author_replied_in_thread` · `followed_by_change_in_path` ·
`follow_evidence` · **`is_intervention_candidate`** · `body_text` (retained
verbatim so any claim can be shown to the reader)

### `reviewer_intervention_rollup` — **`actor_id`**
`substantive_comments` · `distinct_prs` · `distinct_authors_helped` ·
`distinct_components` · `safety_comments` · `safety_category_counts{}` ·
`resolved_threads` · `followed_by_change`

> Counts only. Turning these into a reviewer ranking is a Phase 2 decision that
> must be defended in the UI.

### `pr_anomalies` — **`pr_number`**
`anomaly_flags[]` · `anomaly_count` · `has_anomaly` · reconciliation detail
(`github_changed_files` vs `git_changed_files` + `changed_files_delta` +
`changed_files_mismatch`, same for additions/deletions) ·
`reconciliation_possible` · `reconciliation_skipped_reason` ·
`pr_lifetime_hours`

### `dependency_edges` / `module_nodes` / `component_edges`
`dependency_edges`: **`source_path`** · **`target_path`** · `specifier` ·
`resolution` · `language` · `kind` · `is_type_only` · `is_dynamic` ·
`source_component` · `target_component` · `crosses_component`

`module_nodes`: **`path`** · `language` · `component` · `fan_in` · `fan_out` ·
`is_hub` · `cross_component_fan_in` · `parse_status` · `parse_error` ·
`has_dynamic_imports`

`component_edges`: **`source_component`** · **`target_component`** ·
`edge_count`

---

## 6. PostHog-specific facts Phase 2 will get wrong without

These were discovered empirically against the live repository. They are the
difference between a credible dashboard and a wrong one.

**6.1 — `merged_by` is almost always a bot.** PostHog merges through a Trunk
merge queue, so `merged_by_login` is `trunk-io` on essentially every merged PR.
It is not a signal about a human.

**6.2 — The merge queue opens throwaway PRs.** Titles shaped
`trunk-merge/pr-83501/<uuid>` are authored by `trunk-io`, are draft, and close
without merging. They are ~40% of "PRs created" in the window. They are
labelled `is_merge_queue_artifact = true` and `ranking_eligible = false`. Count
them and you will roughly double every engineer's apparent output.

**6.3 — Squash merges.** One merged PR = exactly one commit on `master`. There
is no intra-PR commit history on the analysed branch, so "commits per engineer"
is the same number as "PRs per engineer" and carries no extra information.

**6.4 — AI co-authorship is widespread and is not bot authorship.** A large
share of commits carry `Co-authored-by: … noreply@anthropic.com` or
`cursoragent@cursor.com`. `commits.has_ai_co_author` marks them. The PR was
still opened, defended in review, and merged by a human. Treat it as context to
disclose, not as a discount — and note that engineers who use assistants
heavily will show inflated raw volume, which is precisely why volume is a poor
impact measure.

**6.5 — Conventional titles are near-universal (~99.9%) and therefore weak as a
discriminator.** `title_prefix` is good for grouping, not for ranking. Prefer
`title_claim_corroborated` where you need signal.

**6.6 — `.github/CODEOWNERS-soft` does not exist** in this repository, and
CODEOWNERS itself is deliberately tiny (the file argues against its own use).
Real ownership lives in 26 distributed `owners.yaml` files. `pr_files.owners`
already resolves this; do not re-derive it from CODEOWNERS.

**6.7 — `ee/**` is not MIT.** `license_area` marks it. Do not aggregate it as
ordinary open-source contribution without saying so.

**6.8 — The dataset is exactly 90 days, by construction.** Every PR in
`pull_requests` was either merged inside the window or opened inside it. PRs
that were merely *updated* during the window are deliberately excluded: on this
repository that cohort added 2,084 PRs, the oldest opened 2020-02-09, which
would have made "90 days of data" untrue. The only artifacts that may predate
the window are ones an in-window PR explicitly references, and those carry
`context_only = true`.

---

## 7. Known gaps Phase 2 must not paper over

Read `run_manifest.json.known_gaps` at run time. Structural gaps:

| Gap | Consequence |
|---|---|
| Clone is `--shallow-since` (~30 days before window start) | No pre-window history. Survival/reachability look *forward* only; a PR whose merge commit predates the clone has `has_merge_commit_in_clone = false` and no `pr_files`. |
| Rust / Go / SQL / Hog imports are not parsed | Those files are graph nodes with `parse_status = 'not_parsed'`; blast radius for Rust-only changes is `unknown`, not small. |
| Dynamic imports and runtime registries are invisible | `module_nodes.has_dynamic_imports` flags the files; reach is a lower bound. |
| Review threads beyond the pagination cap | `review_threads.comments_truncated = true` on affected threads. |
| `survival_*` past the window end | `NULL` with a `_reason`. Never read as 0. |
| Semantic consequence of review comments | Deliberately out of scope for Phase 1 — that is Phase 2's work. |

---

## 8. Worked example

Substantive review interventions per person, cluster-aware, eligible PRs only:

```sql
WITH eligible AS (
  SELECT pr_number FROM read_parquet('artifacts/pull_requests.parquet')
  WHERE ranking_eligible
),
person AS (
  SELECT actor_id, identity_cluster_id, login, is_bot
  FROM read_parquet('artifacts/actors.parquet')
)
SELECT
  p.identity_cluster_id,
  any_value(p.login)                                   AS login,
  count(*)                                             AS substantive_comments,
  count(DISTINCT c.pr_number)                          AS prs_touched,
  count(DISTINCT c.pr_author_actor_id)                 AS authors_helped,
  count(*) FILTER (WHERE c.has_safety_vocabulary)      AS safety_comments,
  count(*) FILTER (WHERE c.followed_by_change_in_path) AS followed_by_change
FROM read_parquet('artifacts/review_intervention_candidates.parquet') c
JOIN eligible e USING (pr_number)
JOIN person   p ON p.actor_id = c.commenter_actor_id
WHERE c.is_intervention_candidate AND NOT p.is_bot
GROUP BY p.identity_cluster_id
ORDER BY substantive_comments DESC;
```

Every row is traceable: `review_intervention_candidates.url` links to the
comment, and `body_text` holds the text the claim rests on. A dashboard that
shows a number should be able to show that comment.

---

## Changelog

| Schema version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-17 | Initial contract. 16 normalized + 11 derived tables. |
