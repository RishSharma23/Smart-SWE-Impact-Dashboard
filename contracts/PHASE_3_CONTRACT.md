# Phase 3 Consumption Contract — the static dashboard package

**Status: FROZEN at export schema version `1.0.0`.** Phase 3 (UI + hosting) may
be built against this document today. Any change to a field name, type or
meaning below requires an `EXPORT_SCHEMA_VERSION` bump in
`src/impact2/versions.py` and a [Changelog](#changelog) entry.

> **Phase 3 can start now.** The shapes below are final and a fixture package
> is committed at `docs/fixtures/phase3/` (see §11). Build against the fixtures;
> swap in the real package when Phase 2's run completes. Nothing in the UI
> should need to change.

---

## 1. What Phase 3 reads

Everything, and only, from `artifacts/phase3/`. Nothing under `data/` and
nothing from Phase 1 is contract for the UI.

```
artifacts/phase3/
  dashboard_manifest.json     <- read this FIRST (§2)
  rankings.json               <- scenarios, positions, tiers, stability (§4)
  engineers.json              <- profile-safe fields, dimension profile (§5)
  episodes.json               <- narratives, dimensions, roles, evidence (§6)
  comparisons.json            <- pairwise material for each top five (§7)
  claims.json                 <- every renderable sentence (§3)
  evidence.json               <- shard index
  evidence/<kind>.json        <- sharded artifacts: pull_request, issue,
                                 commit, review_comment, file, feature_flag, doc
  methodology.json            <- rubric, weights, thresholds, formulas (§8)
  coverage.json               <- missingness, limitations, validation (§9)
  indexes.json                <- precomputed inverted indexes (§10)
```

All files are UTF-8 JSON, minified, and safe to serve from any static host.
There is **no server, no database and no model call at render time.** A build
step that inlines these files into the bundle is fine; so is `fetch()` at
runtime.

---

## 2. Read `dashboard_manifest.json` first

```jsonc
{
  "manifest_version": "1.0.0",
  "generated_at": "2026-08-17T…Z",
  "methodology_version": "1.0.0",
  "title": "PostHog observable repository impact",
  "subtitle": "Explainable impact analytics over a 90-day public GitHub window",

  "source": {
    "repository_url": "https://github.com/PostHog/posthog",
    "analyzed_head_sha": "d4295d57…",      // MUST be displayed
    "is_shallow_clone": true
  },
  "window": { "start": "…Z", "end": "…Z", "lookback_days": 90 },  // MUST be displayed
  "phase1_provenance": { … },              // input hashes, verification status

  // Describes THE ANALYSIS, not the package. Unchanged by the export mode.
  "counts": { "episodes": 0, "engineers": 0, "rankable_engineers": 0,
              "claims": 0, "dimension_assessments": 0, "participants": 0,
              "propagation_edges": 0, "review_interventions": 0 },

  "export_mode": "projection" | "full",    // §2.2
  "projection": { "rule": "…",
                  "episodes_included": 0, "episodes_omitted": 0,
                  "episode_pages": 0, "episode_pages_truncated": 0,
                  "claims_included": 0, "claims_omitted": 0,
                  "evidence_artifacts_included": 0,
                  "evidence_artifacts_omitted": 0 },
  "render_plan": { "episode_pages": 250,
                   "episode_page_ids": [ … ],   // in priority order
                   "episode_pages_truncated": 0,
                   "per_engineer": { "featured": 8, "current": 6,
                                     "foundational": 6, "other": 40 },
                   "rule": "…" },

  "files":   { "<name>": { "path", "bytes", "sha256", "rows" } },
  "indexes": { "file": "indexes.json", "available": [ … ] },

  "validation_status": "pass" | "pending_human_review" | "fail",
  "publishable": false,
  "publishable_blockers": [ { "item", "status", "queue_file" } ],
  "safety_scan": { "status": "pass", "violations": [] },

  "limitations_headline": "…",             // MUST be displayed
  "ui_contract": { … }                     // §3, machine-readable
}
```

### 2.1 `publishable` is a hard gate

`publishable` is `false` until every human-gated validation item is signed off
(cluster audit, review-causality verification, regression-link verification,
and approval of all five finalist profiles). **A build must not deploy to a
public URL while `publishable` is `false`.** Rendering it locally or on an
internal preview is fine and expected — put a persistent banner on the page:

> Provisional — this run has not passed human review. Blockers: *<item list>*.

The blockers array tells you exactly which. This is a deliberate design
decision, not a bug to work around.

### 2.2 The package is a projection by default, and it says which

The pipeline produces far more than the dashboard renders. On the reference
repository the complete package is 187 MB, of which the site puts a few per cent
on a page. `export.mode` in `config/phase2/export.yaml` therefore selects:

| Mode | What ships |
|---|---|
| `projection` (default) | the records a rendered surface resolves |
| `full` | everything the pipeline produced |

**A projection is not a summary.** Nothing is rounded, aggregated or truncated.
A record is in the package exactly as the pipeline produced it, or it is not in
the package and `projection.*_omitted` says how many are missing and
`projection.rule` says by which rule. A consumer must never present an omitted
record as absent from the *analysis*: `counts` describes the analysis and is
identical in both modes, which is why the two blocks are separate.

What this means when you read the package:

1. **`render_plan.episode_page_ids` is authoritative.** Generate a detail page
   for exactly those ids, in that order. Do not re-derive the list: Phase 2
   shipped the episodes that list names, and a locally derived list is free to
   name an episode the package does not carry. `episode_pages_truncated` is the
   number of candidates past the cap, and should be shown where it matters.
2. **`render_plan.per_engineer` is the cap your profile page must use.** The
   package carries each contributor's featured, current, foundational and other
   episodes up to those numbers and no further. Rendering more is asking for
   records that are not there; rendering fewer wastes what was shipped.
3. **Referential integrity is scoped to what is rendered.** In `projection`
   mode, an `episode_id` referenced from `engineers.json` beyond those caps may
   be absent, and so may every claim on an episode that has no page except its
   `title_claim_id`. Both are expected. Check what you will render, and fail the
   build on a dangling reference *there*, which is still a real defect.
4. **`evidence/` follows the episodes.** An artifact is shipped when an included
   episode references it.

Rebuild with `export.mode: full` when you want the complete record, for example
to do your own analysis over the claim set. The same UI renders both, because
both carry the same plan.

---

## 3. The claim contract — the one rule that matters

**Every human-readable sentence the UI renders must be a `claim_id` lookup in
`claims.json`. Rendering a string that is not a claim is a contract violation.**

```jsonc
// claims.json
{
  "claims": [
    {
      "claim_id": "claim/8f3a1c…",         // content-addressed, stable
      "text": "The change landed on the default branch. Release is not …",
      "claim_type": "episode_narrative" | "dimension_band" | "attribution" |
                    "portfolio" | "ranking" | "stability" | "limitation" |
                    "counterevidence",
      "subject": "<episode_id | participant_id | portfolio_id | …>",
      "evidence": [
        { "artifact_id": "github.com/PostHog/posthog#pr/12345",
          "url": "https://github.com/PostHog/posthog/pull/12345",
          "kind": "pull_request",
          "detail": "state=MERGED merged_at=…" }
      ],
      "evidence_count": 3,
      "evidence_is_methodological": false, // true only for limitation claims
      "derivation": "dimensions.rubric:product_outcome (rubric 1.0.0)",
      "confidence": "high" | "medium" | "low" | null
    }
  ],
  "count": 0,
  "correction_pathway": { "enabled": true, "instructions": "…", "contact_field": "claim_id" }
}
```

### Required UI behaviour

1. Build a `Map<claim_id, claim>` once at load.
2. Anywhere a `*_claim_id` field appears, render `claim.text`.
3. Every rendered claim must expose its evidence — a popover, a footnote, an
   expandable row; the shape is your choice, the *availability* is not. Each
   evidence entry has a real GitHub URL; link it.
4. Show the `claim_id` itself somewhere reachable (a copy button, a `title=`,
   a details panel). The correction pathway is "quote the claim_id", so a
   reader has to be able to get it.
5. Fields named `title`, `label`, `status`, `dimension`, enum values and
   numbers are **chrome**, not claims — render those directly. Chrome is
   anything that is the same for every row; prose about a specific engineer or
   episode is always a claim.

If a `*_claim_id` is `null`, the claim was rejected for lacking traceable
evidence. Render nothing — not a placeholder, not the raw field.

---

## 4. `rankings.json`

```jsonc
{
  "default_scenario": "balanced",
  "scenarios": [
    {
      "scenario": "balanced",
      "label": "Balanced",
      "description": "…",
      "available": true,
      "unavailable_reason": null,        // set when available=false
      "remedy": null,                    // exact command to make it available
      "note": null,                      // e.g. "identical to the unfiltered run"
      "weights": { "product_outcome": 0.22, … },   // MUST be exposed in the UI
      "thresholds": { "<criterion>": { "q": 0.0, "p": 1.0, "v": 3.0 } },
      "alternatives": 42,
      "excluded_insufficient_evidence": 118,
      "positions": [
        {
          "position": 1,
          "tier": 1,                     // equal tier = NOT distinguishable
          "actor_cluster_id": "github/user/someone",
          "login": "someone",
          "dimension_values": { "<criterion>": 3.2 | null },
          "incomparable_with": [ "<actor_cluster_id>", … ],
          "incomparable_count": 3,
          "cross_check_position": 2,     // PROMETHEE II position
          "cross_check_delta": 1,
          "stability": {
            "rank_stability_index": 0.86,
            "top5_inclusion_probability": 0.94,
            "position_range": [1, 4]
          }
        }
      ],
      "cross_check": { "method": "promethee_ii", "top5_agreement": 0.8, "note": "…" }
    }
  ],
  "method": { "name": "ELECTRE III", "cross_check": "PROMETHEE II",
              "why_not_a_score": "…", "tiers_explained": "…" }
}
```

### Eight scenarios, two of them unavailable on a 90-day window

`last_12_months` and `foundational_full_history` ship with
`available: false`, an `unavailable_reason` and a `remedy` (the exact command
that would make them available). **Render them as disabled tabs with the reason
visible** — do not hide them, and do not silently fall back to the balanced
scenario. The whole point is that the reader can see what was not measurable.

### Rendering rules

* **Tiers over positions.** Two engineers in tier 2 are *not distinguishable on
  this evidence*. Show the tier as the primary grouping and the position as a
  secondary detail. Do not draw a podium that implies a strict order inside a
  tier.
* **`incomparable_with` is a feature.** If A and B are incomparable, say so
  where they meet.
* **Always show `dimension_values` alongside a position**, and show `null`
  as "not assessable", never as 0 or as an empty bar. See §5.2.
* **`cross_check_delta != 0` deserves a marker.** It means the two aggregation
  methods disagree, which is real information about how firm the position is.
* **Never render a composite score.** There isn't one. If you find yourself
  wanting a single number for a sort key, sort by `position` and `tier`.

---

## 5. `engineers.json`

```jsonc
[
  {
    "actor_cluster_id": "github/user/someone",     // join key everywhere
    "login": "someone",
    "display_name": "Someone",
    "profile_url": "https://github.com/someone",
    "avatar_url": "https://github.com/someone.png",
    "affiliation": "unknown",                      // ALWAYS unknown; see note
    "affiliation_note": "…",
    "identity_ambiguity": "resolved" | "ambiguous",
    "identity_ambiguity_reasons": [ … ],

    "portfolio_id": "portfolio/…",
    "thesis_claim_ids": [ "claim/…", … ],          // the profile's prose

    "dimension_profile": [
      {
        "dimension": "product_outcome",
        "value": 3.15 | null,                      // null = NOT ASSESSABLE
        "interval": [2.6, 3.6],
        "confidence": "high" | "medium" | "low" | "unknown",
        "is_unknown": false,
        "unknown_reason": null,
        "top_episode_id": "…#episode/…",
        "episode_count": 4,
        "aggregation_trace": [                     // show this on demand
          { "rank": 1, "value": 3.0, "coefficient": 1.0, "contribution": 3.0 },
          { "rank": 2, "value": 2.0, "coefficient": 0.55, "contribution": 1.1,
            "headroom_capped": true }
        ]
      }
    ],
    "strongest_dimension": "engineering_leverage",
    "strongest_evidence_episode_id": "…#episode/…",

    "episode_ids": [ … ],
    "current_episode_ids": [ … ],                  // recent work
    "foundational_episode_ids": [ … ],             // persistent / high-leverage
    "roles_held": [ "core_implementer", "risk_preventer", … ],
    "concentration_profile": "single_episode_dominant" | "few_episodes" | "broad",
    "diversity_affects_ranking": false,            // it is descriptive only

    "active_period": { "first_observed", "last_observed", "span_days", "note" },

    "rankable": true,
    "eligibility_label": null | "insufficient_observable_evidence",
    "eligibility_reasons": [ … ],

    "uncertainty": {
      "rank_stability_index": 0.86,
      "top5_inclusion_probability": 0.94,
      "position_range": [1, 4],
      "claim_id": "claim/…"
    }
  }
]
```

### 5.1 `affiliation` is always `"unknown"`

Public GitHub does not reliably distinguish employees from community
contributors. Do not label anyone "PostHog engineer". Render the note.

### 5.2 `null` is not zero — this is the most important rendering rule

A `null` dimension value means *we could not assess this*, usually because the
work is in a language with no import parser, or the window ended before the
durability checkpoint. It is **not** a low score.

| Wrong | Right |
|---|---|
| A radar chart with the axis at 0 | Break the axis; grey the spoke; label "not assessable" |
| A bar of length 0 | A hatched/empty slot with the `unknown_reason` on hover |
| Sorting nulls last as if worst | Excluding them from the sort and saying so |

The same rule applies to `interval`: a wide interval means uncertain, not bad.

### 5.3 Engineers with `rankable: false`

They appear in `engineers.json` but not in any ranking. Give them a section of
their own — "insufficient observable evidence" — with `eligibility_reasons`
rendered. This is a statement about the data, and the UI must say so.

---

## 6. `episodes.json`

```jsonc
[
  {
    "episode_id": "github.com/PostHog/posthog#episode/12345-8f3a1c2b4d5e",
    "title_claim_id": "claim/…",
    "problem_claim_id": "claim/…",
    "intervention_claim_id": "claim/…",
    "outcome_claim_id": "claim/…",
    "title": "…",                       // convenience copy; prefer the claim

    "started_at": "…Z", "ended_at": "…Z", "duration_days": 12.4,

    "status": "shipped_observable" | "partial_or_behind_flag" | "reverted" |
              "superseded" | "maintenance" | "exploratory" | "unknown",
    "status_reasons": [ … ],
    "release_corroboration": "corroborated" | "merged_only",   // MUST be shown
    "release_evidence": [ { "kind", "detail" } ],

    "components": [ … ], "products": [ … ],
    "reachability_band": "local" | "component" | "cross_product" |
                         "platform_wide" | "unknown",
    "feature_flag_keys": [ … ],
    "pr_numbers": [ … ], "issue_numbers": [ … ],

    "cluster_confidence": 0.83,
    "cluster_confidence_reasons": [ … ],
    "sub_episode_links": [ { "child_pr", "parent_pr", "relation", "evidence" } ],
    "counterevidence": [ { "kind", "evidence_tier", "requires_human_confirmation",
                           "detail", "pr_number" } ],
    "has_ai_co_author": true,
    "touches_enterprise_licensed_code": false,

    "dimensions": [
      { "dimension", "band" /* 0-4 or null */, "band_label", "is_unknown",
        "unknown_reason", "confidence", "confidence_reasons",
        "corroboration_status", "artifact_classes", "evidence",
        "counterevidence", "rationale_claim_id" }
    ],
    "participants": [
      { "actor_cluster_id", "login", "roles", "share_category",
        "share_reasons", "attribution_confidence", "direct_evidence",
        "has_profile", "claim_ids" }
    ],
    "artifact_ids": [ … ],              // resolve via evidence/<kind>.json
    "analytics": {
      "propagation": { "reach_file_count", "reach_pr_count", "adoption_events",
                       "edges_recorded", "walk_truncated",
                       "distinct_component_penetration", "components_reached",
                       "distinct_downstream_authors", "max_path_depth",
                       "mass_after_cap", "cap_applied", "source_age_days",
                       "raw_decay_factor", "persistence_detected",
                       "effective_decay_factor", "reason" },
      "novelty": { "novelty_class", "rationale", "markers", "uncertainty" },
      "corrective_burden": { "by_class", "capped_penalty", "confirmed_revert",
                             "unconfirmed_event_count" }
    }
  }
]
```

### 6.1 `release_corroboration` must appear next to `status`

`shipped_observable` means *it landed on `master`*. It does **not** mean users
saw it. When `release_corroboration` is `merged_only`, the episode card must
carry a qualifier — "merged; release not independently corroborated". Phase 2
enforces the same distinction internally (no dimension reaches band 3 without
corroboration); the UI must not undo it visually.

### 6.2 Counterevidence is not optional chrome

Every episode card that shows evidence must also show `counterevidence` when it
is non-empty. An entry with `requires_human_confirmation: true` must be
rendered with that caveat attached — those are Phase 1's deliberately
low-precision recall signals and calling one a regression in the UI would be
wrong.

### 6.3 `has_profile: false` means credited but not profiled

A portfolio is only built for participants who contribute to one, so a
co-author credited as `supporting` is a real contributor with real evidence and
**no entry in `engineers.json`**. They are kept in the episode rather than
dropped — removing them would erase people who did the work — and flagged.

Render the name and their evidence; do not link to a profile page and do not
treat the missing engineer record as a data error.

### 6.4 Propagation numbers are aggregates; the edges are examples

`adoption_events` counts **every** downstream adoption the walk found;
`edges_recorded` is how many of them were materialised as full records in
`evidence/`. The aggregates (`reach_file_count`,
`distinct_component_penetration`, `distinct_downstream_authors`,
`max_path_depth`, `mass_after_cap`) are computed over all events, not over the
sample. Render the aggregate as the number and the recorded edges as
"examples", never as an exhaustive list.

`walk_truncated: true` means the walk hit its event or frontier cap. Show the
reach as a lower bound when it is set — the same way `reachability_band:
"unknown"` is shown, not silently.

### 6.5 `share_category`, never a percentage

`primary` / `material` / `supporting` / `unclear`. There is no percentage in
the package and you must not compute one. `unclear` means the evidence did not
settle it — render it as such, not as "0%".

---

## 7. `comparisons.json`

```jsonc
{
  "scenarios": {
    "balanced": {
      "top_five": [ "<actor_cluster_id>", … ],
      "pairwise": [
        {
          "a", "b", "a_login", "b_login",
          "concordance": 0.72,
          "credibility": 0.68,
          "per_criterion": [
            { "criterion", "a_value", "b_value", "difference", "weight",
              "concordance", "discordance", "thresholds": {"q","p","v"} }
          ],
          "excluded_criteria": [ { "criterion", "reason",
                                   "a_unknown_reason", "b_unknown_reason" } ],
          "vetoing_criteria": [ … ],
          "counterevidence_veto": false,
          "explanation_claim_id": "claim/…"
        }
      ],
      "methodology_trace": "…"
    }
  }
}
```

Every ordered pair among the top five is present in both directions. This is
the "publish a methodology trace for every top-five pairwise outcome"
requirement — a "why is A above B?" affordance is expected in the UI.
`excluded_criteria` must be shown; it is where "unknown ≠ zero" becomes visible.

---

## 8. `methodology.json`

Full rubric, attribution rules, criterion weights and thresholds, all analytics
parameters, and the literal formulas:

```jsonc
{
  "methodology_version": "1.0.0",
  "impact_definition": "…",
  "unit_of_analysis": "impact episode (a connected initiative arc), not the commit or the pull request",
  "rubric": { … },            // every band rule, in English
  "attribution": { … },       // roles, evidence requirements, relevance matrix
  "outranking": { … },        // weights, q/p/v, distillation, scenarios
  "analytics": { … },         // decay half-life, hub damping, corrective classes
  "episode_construction": { … },
  "eligibility": { … },
  "formulas": {
    "portfolio_aggregation": "value = min(scale_max, v1 + min(headroom, sum(coeff_i * v_i for i >= 2))) …",
    "time_decay": "exp(-ln(2) * age_days / half_life_days)",
    "hub_damping": "path_weight = 1 / (1 + log2(1 + fan_in))",
    "edge_combination": "pair_weight = 1 - prod(1 - edge_strength_i)  (noisy-OR)",
    "concordance": "…", "discordance": "…", "credibility": "…"
  },
  "explicitly_not_used": [ "commit count", "pull-request count", "lines of code",
                           "review count", "velocity ratios",
                           "any 0-1000 composite score", "per-day normalisation",
                           "gradient-descent-learned weights" ],
  "llm": { "provider", "model", "available", "role", "usage", "pending_queue", … }
}
```

Ship a **Methodology page** that renders this file. `explicitly_not_used` is
worth surfacing prominently — it is the clearest short answer to "is this a
productivity tracker?".

`llm.available` is `false` when no provider was configured. Say so plainly:
"the optional semantic layer did not run; all analysis is deterministic". Never
imply an LLM produced the ranking — it never does, even when it runs.

---

## 9. `coverage.json`

```jsonc
{
  "phase1": { … },                   // input verification: hashes, row counts
  "known_gaps": [ { "gap", "detail", "consequence", "severity" } ],
  "capabilities_disabled": { "<table>": "<what it disables>" },
  "summaries": { … },                // per-stage statistics
  "validation": { "status", "publishable", "publishable_blockers", "items": [ … ] },
  "limitations": {
    "headline": "…",                 // MUST be displayed on the landing page
    "items": [ … ],
    "claim_ids": [ … ],
    "correction_pathway": { "enabled", "instructions", "contact_field" }
  },
  "missingness": {
    "dimension_unknown_rates": { "<dimension>": { "assessed", "unknown", "unknown_rate" } },
    "episodes_without_diff": 0,
    "episodes_without_release_corroboration": 0,
    "engineers_below_evidence_bar": 0,
    "note": "…"
  }
}
```

`limitations.headline` is required on the landing page, not buried in a footer.
`capabilities_disabled` tells you which parts of the UI to grey out: e.g. when
`review_intervention_candidates` is absent, collaborative amplification is
unknown for everyone and the dimension should be visibly disabled rather than
rendered as a row of zeros.

---

## 10. `indexes.json`

Precomputed inverted indexes so filters never scan:

```jsonc
{
  "episodes_by_component":            { "<component>": ["<episode_id>", …] },
  "episodes_by_status":               { "<status>":    ["<episode_id>", …] },
  "episodes_by_engineer":             { "<actor_cluster_id>": ["<episode_id>", …] },
  "engineers_by_role":                { "<role>": ["<actor_cluster_id>", …] },
  "engineers_by_strongest_dimension": { "<dimension>": ["<actor_cluster_id>", …] }
}
```

---

## 11. Fixtures — build against these today

`docs/fixtures/phase3/` holds a complete, tiny, **synthetic** package with the
exact shapes above: 3 engineers, 5 episodes, 2 scenarios (one unavailable), a
full claim set, sharded evidence and a `publishable: false` manifest with
blockers. It exercises every rendering rule that is easy to get wrong:

* an engineer with `rankable: false`,
* a dimension with `value: null` and an `unknown_reason`,
* an episode with `release_corroboration: "merged_only"`,
* an episode with `counterevidence` including `requires_human_confirmation`,
* a pair with an `excluded_criteria` entry,
* two engineers sharing a tier and listed as `incomparable_with` each other,
* a scenario with `available: false` plus its `remedy`.

Point the app at `docs/fixtures/phase3/` in development and at
`artifacts/phase3/` in production. **No code change should be required to
switch** — if one is, the UI has coupled to fixture-specific data.

---

## 12. Hosting

The package is static and self-contained.

* **Size.** The full package is dominated by `evidence/` and `claims.json`.
  Load `dashboard_manifest.json` + `rankings.json` + `engineers.json` eagerly;
  lazy-load `episodes.json`, `comparisons.json` and each `evidence/<kind>.json`
  on demand. `manifest.files[*].bytes` gives you exact sizes for budgeting.
* **Integrity.** Every file carries a `sha256` in the manifest. Verifying it in
  a build step catches a partially-copied deploy.
* **Caching.** Content is immutable per run. Hash-stamp filenames or set a long
  `max-age` on everything except `dashboard_manifest.json`.
* **No secrets.** The export runs a scan for tokens, local paths, emails and
  forbidden inference fields before writing, and refuses to mark a package
  publishable if it finds any. Do not add any.
* **External links.** Every evidence URL points at `github.com`. Use
  `rel="noopener noreferrer"`.
* **Avatars** are `https://github.com/<login>.png`. If a strict CSP is in play,
  proxy or inline them; do not fall back to a generated identicon that could be
  mistaken for a real photo.

---

## 13. Things the UI must never do

1. Render a string that is not a claim.
2. Show a composite impact score, or invent one to sort by.
3. Show shared credit as a percentage.
4. Render `null` as `0`, or sort unknowns as if they were worst.
5. Say "shipped to users" when `release_corroboration` is `merged_only`.
6. Call a `requires_human_confirmation` candidate a regression.
7. Hide unavailable scenarios instead of disabling them with a reason.
8. Deploy publicly while `publishable` is `false`.
9. Describe anyone's ability, effort, seniority or productivity. The subject of
   every sentence is the *work*, not the person.
10. Label the result anything other than **observable repository impact**.
11. Present a projected package as though it were the whole analysis. `counts`
    is what was analysed, `projection` is what shipped, and the difference
    belongs on the coverage page (§2.2).

---

## Changelog

| Export schema | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-17 | Initial contract. 8 core files + sharded evidence + indexes. |
| 1.0.0 | 2026-08-19 | Additive: `export_mode`, `projection` and `render_plan` in the manifest, `package` in `coverage.json` (§2.2). No field changed meaning, so the schema version is unchanged. The projection is the default; `export.mode: full` restores the previous contents. |
