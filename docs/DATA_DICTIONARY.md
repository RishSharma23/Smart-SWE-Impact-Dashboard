# Data Dictionary

Every table this pipeline emits, with column semantics and — where it matters —
what a `NULL` means.

Machine-readable JSON Schema for each table is generated into `schemas/` by
`make export`. This document is the human-readable companion; where they
disagree, the schema is authoritative for *types* and this document is
authoritative for *meaning*.

---

## Conventions used throughout

| Convention | Meaning |
|---|---|
| **Bold** column | part of the primary key or a join key |
| `*_at` | ISO-8601 UTC string ending `Z` |
| `*_actor_id` | foreign key into `actors.actor_id` |
| `*_version` | version of the code that produced the row |
| `computed_at` | wall-clock stamp; **excluded from content hashes** |

### Null semantics — the rule that matters most

`NULL` ≠ `0` ≠ `false` ≠ `[]`.

* `NULL` = **not recorded, or not knowable**. Always paired with a `*_reason`
  column where a reason exists.
* `0` = measured, and the measurement was zero.
* `[]` = measured, and nothing matched.
* `false` = measured, and the predicate did not hold.

Examples enforced by tests:

| Column | `NULL` means |
|---|---|
| `pr_files.additions` | file is binary → see `line_counts_unavailable_reason` |
| `pr_regression_candidates.survival_30d` | window ended before day 30 → see `survival_30d_reason` |
| `pr_change_shape.title_claim_corroborated` | the prefix makes no checkable claim (e.g. `chore`) |
| `pr_blast_radius.changed_fan_in_total` | no changed file is a graph node |

Aggregations must exclude nulls explicitly. Coalescing them to zero converts
"we don't know" into "it's nothing", which is the exact failure this pipeline
is built to avoid.

---

# Normalized tables

## `actors` — one row per identity

| Column | Type | Notes |
|---|---|---|
| **`actor_id`** | string | `github/user/<login>`, or `git/email/<sha256[:16]>` when no GitHub account is linked |
| `login` | string? | `NULL` for Git-only identities |
| `display_name` | string? | GitHub name, else first Git author name seen |
| `github_database_id` | int? | stable numeric GitHub id |
| `github_typename` | string? | `User` or `Bot` as reported by GraphQL |
| `account_type` | string | `user` \| `bot` \| `git_identity` |
| `bot_probability` | float | 0.0–1.0 |
| `bot_reasons` | string[] | which rules fired, with their text |
| `is_bot` | bool | `bot_probability >= 0.9` |
| `is_ai_assistant_identity` | bool | Claude / Cursor / Codex co-author identity. **Separate axis from `is_bot`** |
| `emails` | string[] | only addresses already public in Git history |
| `git_names` | string[] | every display name seen |
| **`identity_cluster_id`** | string | **group on this, not `actor_id`, for per-person totals** |
| `identity_cluster_size` | int | members in the cluster |
| `identity_cluster_members` | string[] | the member `actor_id`s |
| `ambiguity_status` | string | `resolved` \| `ambiguous` |
| `ambiguity_reasons` | string[] | e.g. `shared_email:alice,bob`, `bot_classification_uncertain` |
| `sources` | string[] | where the identity was observed (`git_author`, `pr_author`, `review`, …) |

**Clustering merges only on evidence that is actually unique**: GitHub login,
normalised email, and the numeric id inside
`12345+login@users.noreply.github.com`. Display name is **never** a merge key.
An email mapping to several logins is recorded as ambiguity, not merged.

## `pull_requests` — one row per PR (the spine)

*Identity and state*

| Column | Notes |
|---|---|
| **`pr_number`**, `pr_id`, `url`, `node_id`, `database_id` | `pr_id` is repository-qualified |
| `title_raw`, `body_text`, `body_length` | raw title always retained |
| `state` | `MERGED` \| `CLOSED` \| `OPEN` |
| `is_draft` | |
| `created_at`, `updated_at`, `closed_at`, `merged_at` | |
| **`author_actor_id`**, `author_login`, `author_typename` | |
| `author_is_bot`, `author_bot_probability` | denormalised from `actors` |
| `merged_by_actor_id`, `merged_by_login` | **almost always `trunk-io`** — a merge queue, not a human |
| `base_ref`, `head_ref`, `base_sha`, `head_sha`, `merge_commit_sha` | |
| `is_cross_repository`, `head_repository_owner` | fork PRs |
| `labels`, `label_count`, `assignee_logins`, `participant_logins`, `milestone_title` | |

*Parsed title*

| Column | Notes |
|---|---|
| `title_prefix` | normalized type: `feat`/`fix`/`chore`/… `NULL` if not conventional |
| `title_prefix_raw` | as written, before normalisation |
| `title_scope` | text inside `(...)` |
| `title_breaking` | `!` marker or `BREAKING CHANGE:` footer |
| `title_subject` | subject with the `(#12345)` squash suffix stripped |
| `title_parser_status` | `strict` \| `loose` \| `alias` \| `unknown_type` \| `not_conventional` \| `not_a_human_title` |
| `title_parser_confidence` | 0.0–1.0 |
| `title_parser_notes` | why confidence was reduced |
| `title_class` | `merge_queue_artifact` \| `dependency_bump` \| `revert` \| `automation` \| `NULL` |
| `title_squash_pr_number` | number parsed from the `(#N)` suffix |

*Size descriptors — evidence, never scores*

| Column | Notes |
|---|---|
| `github_additions`, `github_deletions`, `github_changed_files` | GitHub's count of the **full branch diff** |
| `git_additions`, `git_deletions`, `git_file_count` | computed from the **squashed merge commit** |
| `has_binary_files` | |
| `is_bulk_change`, `bulk_category`, `bulk_share` | ≥25 files and ≥90% one mechanical category |

The two sources differ legitimately (rebases, merge-queue re-bases); the delta
is reconciled per PR in `pr_anomalies`, never silently resolved.

*Counts* — `review_count`, `review_thread_count`, `comment_count`,
`commit_count`, `reaction_count`.

*Cohort and eligibility*

| Column | Notes |
|---|---|
| `cohorts` | which discovery cohorts found it (`merged`, `created`) |
| `merged_in_window`, `created_in_window` | computed from timestamps, not from which search returned the row |
| `context_only` | referenced from inside the window but anchored outside it |
| `is_merge_queue_artifact` | Trunk throwaway PR |
| **`ranking_eligible`** | `MERGED` **and** merged in window **and** not a merge-queue artifact |
| `ranking_ineligible_reason` | `merge_queue_artifact` \| `not_merged` \| `merged_outside_window` |
| `has_merge_commit_in_clone` | `false` → no `pr_files` rows exist for this PR |

**Bot authorship is not part of eligibility.** A bot-authored PR can be
eligible; use `author_is_bot` to decide, and say what you decided.

## `commits` / `commit_parents`

| Column | Notes |
|---|---|
| **`commit_sha`**, `commit_id`, `tree_sha`, `parent_shas`, `parent_count`, `is_merge_commit` | |
| `author_name`, `author_email`, `author_actor_id`, `authored_at` | |
| `committer_name`, `committer_email`, `committer_actor_id`, `committed_at` | |
| `author_is_committer` | |
| `subject`, `message`, `message_sha256` | |
| `co_authors`, `co_author_actor_ids`, `co_author_count` | from `Co-authored-by:` trailers |
| `has_ai_co_author` | an AI assistant identity appears as co-author |
| `trailers` | parsed trailing-block trailers only, not prose |
| `is_revert`, `revert_of_subject`, `revert_of_sha` | handles **both** `Revert "..."` and PostHog's `revert(scope): ...` |
| `is_cherry_pick`, `cherry_pick_of_sha` | |
| `pr_number` | **from GitHub `mergeCommit.oid`** — authoritative |
| `pr_mapping_source` | `github_merge_commit` |
| `pr_number_from_subject` | from the `(#N)` suffix — corroboration only, so the two can be reconciled |
| `gpg_status`, `in_window`, `has_patch_text`, `patch_unavailable_reason` | |

`commit_parents`: **`commit_sha`** · **`parent_position`** · `parent_sha` ·
`parent_commit_id`.

## `pr_files` — one row per (PR, file)

Sourced from Git, not the API: PostHog squash-merges, so one merged PR is
exactly one commit and its diff is complete.

| Column | Notes |
|---|---|
| **`pr_number`**, **`path`**, `pr_file_id`, `commit_sha` | |
| `old_path`, `new_path` | populated for renames/copies |
| `change_status` | `A` \| `M` \| `D` \| `R` \| `C` \| `T` |
| `similarity_score` | rename/copy similarity 0–100 |
| `additions`, `deletions` | **`NULL` for binary files** |
| `is_binary`, `line_counts_unavailable_reason` | |
| `is_submodule`, `old_blob_sha`, `new_blob_sha` | |
| `language`, `extension`, `path_depth`, `top_level_dir` | `language` may be `unknown` — a real value |
| `categories`, `risk_surfaces`, `matched_rules` | a path can carry several categories at once |
| `is_test` `is_docs` `is_generated` `is_snapshot` `is_lockfile` `is_vendor` `is_migration` `is_config` `is_binary_asset` `is_styling` `is_localization` `is_ci` | boolean shorthands for `categories` |
| **`component`**, `platform` | `platform` ∈ `product` \| `platform` \| `infrastructure` \| `unknown` |
| `component_source`, `component_rule_priority` (1–6), `component_rule_pattern` | which priority answered, and with what rule |
| `owners`, `owner_source` | from the nearest `owners.yaml`, else CODEOWNERS |
| `license_area` | `MIT` or `PostHog Enterprise (proprietary)` |
| `mapping_uncertainty` | e.g. shared ownership, no owner rule matched |

## `reviews` / `review_threads` / `review_comments` / `comments`

`reviews`: **`review_id`** · **`pr_number`** · `reviewer_actor_id` ·
`reviewer_login` · `reviewer_typename` · `state`
(`APPROVED`/`CHANGES_REQUESTED`/`COMMENTED`/`DISMISSED`) · `submitted_at` ·
`body_text` · `commit_sha` · `url`

`review_threads`: **`thread_id`** · **`pr_number`** · `path` · `line` ·
`start_line` · `original_line` · `diff_side` · `subject_type` · `is_resolved` ·
`is_outdated` · `is_collapsed` · `resolved_by_login` · `resolved_by_actor_id` ·
`comment_count` · **`comments_truncated`** (thread exceeded the page cap) ·
`component` · `owners` · `first_comment_at` · `participant_logins`

`review_comments`: **`comment_id`** · **`thread_id`** · **`pr_number`** ·
`position_in_thread` · `is_thread_opener` · `author_actor_id` · `body_text` ·
`created_at` · `reply_to_id` · `original_commit_sha` · `is_outdated` · `path` ·
`url`

`comments` (PR conversation + issue comments): **`comment_id`** ·
`parent_kind` (`pull_request`\|`issue`) · **`parent_number`** · `parent_id` ·
`author_actor_id` · `body_text` · `created_at` · `url`

## `issues`

**`issue_number`** · `issue_id` · `url` · `title` · `body_text` · `state` ·
`state_reason` · `created_at` · `updated_at` · `closed_at` ·
`author_actor_id` · `labels` · `assignee_logins` · `comment_count` ·
`created_in_window`

## `references` — extracted textual + metadata links

| Column | Notes |
|---|---|
| `source_kind` | `pull_request` \| `issue` |
| **`source_number`**, `source_id` | |
| `reference_kind` | `issue_or_pr` \| `url` \| `feature_flag` \| `edge_phrase` \| `external_artifact` |
| `reference_value` | the number, URL, flag key, or phrase name |
| `reference_subtype` | `github_closing_reference` \| `closing` \| `mention` \| `url_pull` \| `timeline_*` \| `changelog` \| `docs` \| … |
| `strength` | `strong` (GitHub created the link) \| `medium` (explicit number) \| `weak` |
| `source_field` | `title` \| `body` \| `github_metadata` \| `github_timeline` |
| `evidence` | the matched text span |

## `feature_flags`

**`pr_number`** · **`flag_key`** · `detection`
(`constant_resolved`/`api_literal`/`registry_line`/`constant_unresolved`) ·
`diff_side` (`added`/`removed`/`NULL`) · `strength` · `evidence` ·
`owner_annotation` (the `// owner: #team-x` note from PostHog's registry)

## `components` / `path_map`

`components`: **`component`** · `platform` · `label` · `source_rule` ·
`path_glob` · `manifest_path` · `manifest_sha256` · `snapshot_commit`

`path_map`: **`path`** · `component` · `platform` · `component_source` ·
`component_rule_priority` · `component_rule_pattern` · `owners` ·
`owner_source` · `license_area` · `uncertainty` · `is_unclassified` ·
`nearest_agents_file`

## `raw_pages` / `extraction_runs` — provenance

`raw_pages`: **`request_hash`** · `entity` · `shard` · `page_index` ·
`query_name` · `cursor` · `status` · `http_status` · `error` ·
`response_path` · `response_sha256` · `rate_limit_cost` ·
`rate_limit_remaining` · `elapsed_seconds` · `attempt_count` · `extracted_at`

`extraction_runs`: **`run_id`** · `stage` · `status` · `run_started_at` ·
`run_finished_at` · `duration_seconds` · `window_start` · `window_end` ·
`extractor_version` · `schema_version` · `pipeline_version` ·
`python_version` · `platform` · `notes`

---

# Derived evidence tables

## `pr_change_shape` — **`pr_number`**

| Column | Notes |
|---|---|
| `dominant_component`, `dominant_component_share`, `distinct_components`, `distinct_platforms` | |
| `component_entropy` | Shannon entropy over touched components, base 2 |
| `component_histogram`, `category_histogram`, `language_histogram` | |
| `files_product_code` … `files_binary_asset` | per-category counts (13 categories) |
| `files_added` / `_modified` / `_deleted` / `_renamed` / `_copied` / `_type_changed` | |
| `code_file_count`, `generated_or_mechanical_file_count`, `code_share` | quality descriptor only |
| `test_file_count`, `production_file_count` | |
| `test_to_production_links`, `test_to_production_link_count` | matched by **file stem**, not by a files ratio |
| `has_test_changes`, `production_without_test_change`, `tests_without_production_change` | |
| `touches_public_api` `touches_schema` `touches_migration` `touches_auth_privacy` `touches_billing` `touches_ingestion` `touches_data_pipeline` `touches_deployment` `touches_shared_library` `touches_feature_flag_surface` | risk-surface evidence |
| `license_areas`, `touches_enterprise_licensed_code` | |
| **`title_claim_corroborated`**, `title_claim_note` | does the prefix agree with the paths? `NULL` = no checkable claim |

## `pr_blast_radius` — **`pr_number`**

Eight independent signals plus one band. Deliberately **not** a single
"files changed" proxy.

| Group | Columns |
|---|---|
| boundaries | `distinct_components` `distinct_products` `components_touched` `products_touched` `crosses_component_boundary` `crosses_product_boundary` |
| ownership | `distinct_owners` `owners_touched` `crosses_ownership_boundary` `files_without_owner` |
| graph | `graph_covered_files` `graph_coverage_share` `changed_fan_in_total` `changed_fan_in_max` `changed_fan_out_total` `hub_files_touched` |
| downstream | `downstream_file_count` `downstream_component_count` `downstream_components` `downstream_product_count` `reach_depth_limit` |
| surfaces | `risk_surface_count` `touches_shared_library` `touches_platform_surface` |
| verdict | **`reachability_band`** `reachability_uncertainty` `reachability_is_uncertain` |

`reachability_band` ∈ `local` \| `component` \| `cross_product` \|
`platform_wide` \| **`unknown`**.

`unknown` is a real answer. A change confined to Rust/Go/SQL has no parsed
imports; calling it `local` would assert something never measured.

## `candidate_episode_edges` / `candidate_episodes`

Edges: **`source_pr_number`** · **`edge_type`** · **`target_number`** ·
`target_kind` · `target_pr_in_dataset` · `strength` · `evidence_source` ·
`evidence` · `edge_id`

`edge_type` ∈ `closes_issue` `references_pr` `follow_up` `part_of`
`stacked_on` `reverts` `reapplies` `supersedes` `shared_issue`
`shared_feature_flag` `timeline_connected`

Episodes: **`episode_id`** · `pr_numbers` · `pr_count` · `edge_types` ·
`root_pr_number`. Connected components over **medium+** PR↔PR edges only —
weak edges (shared flag) are recorded but never group, because a flag touched
by 30 PRs is not a unit of work.

## `pr_regression_candidates` — **`pr_number`**

| Column | Notes |
|---|---|
| `explicit_regression_signals` | a revert names this PR or its subject |
| `linked_fix_candidates` | later fix sharing an issue or feature flag |
| `proximate_fix_candidates` | later fix touching the same **non-test, non-generated** files inside the proximity window |
| **`regression_evidence_tier`** | `explicit` \| `linked` \| `proximate` \| `none` |
| `requires_human_confirmation` | `true` when only `proximate` evidence exists |
| `was_reverted` | |
| `corrective_churn` | `{self_follow_up, collaborator_follow_up, revert, replacement, unknown}` |
| `files_introduced` | files with status `A` |
| `survival_30d` / `_60d` / `_90d` | share of introduced files still present. **`NULL` + `_reason` when the window ends first** |
| `tests_added_with_fix` | a fix/revert that also added or changed tests |

**The rule this table exists to protect:** a PR is never labelled a regression
solely because a later fix touched the same files. That situation produces
`proximate` + `requires_human_confirmation`, nothing stronger.

## `review_intervention_candidates` — **`candidate_id`**

| Column | Notes |
|---|---|
| `pr_number` `comment_id` `thread_id` `url` `path` `component` | |
| `commenter_actor_id` `commenter_login` `commenter_is_bot` | |
| `pr_author_actor_id` `is_self_comment` | |
| **`substance_class`** | `substantive` \| `nit` \| `acknowledgement` \| `short` \| `bot` \| `empty` |
| `is_substantive`, `substance_reasons`, `matched_nit_pattern`, `matched_ack_pattern` | |
| `safety_categories`, `safety_terms_matched`, `has_safety_vocabulary` | data_loss · migration · security · privacy · performance · scaling · breaking_change · correctness · ux · test_gap |
| `thread_is_resolved` `thread_resolved_by_login` `thread_resolved_by_author` `author_replied_in_thread` | |
| `followed_by_change_in_path`, `follow_evidence` | proxy, not proof — see the caveat below |
| **`is_intervention_candidate`** | substantive **and** not self **and** not bot |
| **`body_text`** | retained verbatim so any claim can be shown to a reader |

Caveat on `followed_by_change_in_path`: squash merging means intra-PR commit
history is absent from `master`, so this leans on thread resolution and the
merged diff rather than per-commit timing.

## `reviewer_intervention_rollup` — **`actor_id`**

`substantive_comments` · `distinct_prs` · `distinct_authors_helped` ·
`distinct_components` · `safety_comments` · `safety_category_counts` ·
`resolved_threads` · `followed_by_change`

Counts only. Turning these into a ranking is a Phase 2 decision that must be
defended in the UI.

## `pr_anomalies` — **`pr_number`**

`anomaly_flags` · `anomaly_count` · `has_anomaly` · `pr_lifetime_hours` ·
reconciliation detail (`github_changed_files` vs `git_changed_files` +
`changed_files_delta` + `changed_files_mismatch`; same for
additions/deletions) · `reconciliation_possible` ·
`reconciliation_skipped_reason`

Flag vocabulary includes: `github_vs_git_file_count_mismatch` ·
`impossible_timestamps_*` · `merged_pr_has_no_resolvable_author` ·
`author_identity_ambiguous` · `bot_authored` · `lockfile_only` ·
`snapshot_only` · `generated_only` · `docs_only` · `test_only` ·
`very_large_file_count:N` · `bulk_mechanical_change:<category>` ·
`title_not_conventional` · `low_confidence_title_parse` ·
`merge_queue_artifact` · `duplicate_tree_sha:N_commits` ·
`cherry_picked_commit` · `commit_author_differs_from_committer` ·
`merge_commit_not_in_local_clone`

## `dependency_edges` / `module_nodes` / `component_edges`

`dependency_edges`: **`source_path`** · **`target_path`** · `specifier` ·
`resolution` (`exact`/`package`/`prefix`/`relative`/`alias`/`baseurl`) ·
`language` · `kind` (`import`/`type_import`/`reexport`/`require`/`dynamic`/`mock`) ·
`is_type_only` · `is_dynamic` · `source_component` · `target_component` ·
`crosses_component`

`module_nodes`: **`path`** · `language` · `component` · `platform` ·
`fan_in` · `fan_out` · `cross_component_fan_in` · `is_hub` (fan-in at or above
the 95th percentile) · `parse_status` (`ok`/`not_parsed`/`parse_error`/`unreadable`) ·
`parse_error` · `has_dynamic_imports`

`component_edges`: **`source_component`** · **`target_component`** ·
`edge_count`

---

## Sidecar files

| File | Contents |
|---|---|
| `<table>.meta.json` | row count, columns, sort keys, **content hash**, hash exclusions, schema version |
| `artifacts/run_manifest.json` | source SHA, window, versions, per-table hashes and row counts, coverage, API cost, gate results, known gaps |
| `artifacts/graph_coverage.json` | import parser coverage by language and resolution class |
| `artifacts/feature_summary.json` | aggregate statistics per feature family |
| `artifacts/component_rules_snapshot.json` | every ownership file read, with its SHA-256 |
| `artifacts/quality_report.json` | full gate output |

Content hashes are computed from **canonicalised row content**, excluding
operational columns — not from Parquet bytes, which embed a writer string that
changes between otherwise-identical runs.
