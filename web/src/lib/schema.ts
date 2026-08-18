/**
 * Zod schemas for the Phase 2 static export (contracts/PHASE_3_CONTRACT.md, 1.0.0).
 *
 * These are deliberately *permissive about extra keys* and *strict about the
 * fields the UI renders*. Phase 2 may add fields without breaking the build;
 * it may not change the meaning of one that is already here.
 */
import { z } from 'zod';

export const DIMENSIONS = [
  'product_outcome',
  'reliability_risk',
  'engineering_leverage',
  'decision_quality',
  'propagation_durability',
  'collaborative_amplification',
] as const;
export type Dimension = (typeof DIMENSIONS)[number];

export const DIMENSION_LABELS: Record<string, string> = {
  product_outcome: 'Product outcome',
  reliability_risk: 'Reliability & risk',
  engineering_leverage: 'Engineering leverage',
  decision_quality: 'Decision quality',
  propagation_durability: 'Propagation & durability',
  collaborative_amplification: 'Collaborative amplification',
};

/**
 * Documented as `high | medium | low | unknown`, but Phase 2 also emits
 * corroboration states here on `episode_narrative` claims (`corroborated`,
 * `merged_only`). Accepting the wider set is deliberate: the UI renders the
 * value it is given rather than failing the build or, worse, silently
 * recolouring "merged_only" as "low confidence". Recorded as a contract
 * deviation in CURRENT_STATE.md.
 */
export const confidence = z.string().nullish();

/** Confidence values the UI knows how to grade. Anything else renders neutrally. */
export const KNOWN_CONFIDENCE = ['high', 'medium', 'low', 'unknown'] as const;

/** Only https://github.com and https://*.github.com evidence links are renderable. */
export const SAFE_URL_HOSTS = ['github.com', 'www.github.com', 'raw.githubusercontent.com'];

export function isSafeUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol === 'https:' && SAFE_URL_HOSTS.includes(u.hostname);
  } catch {
    return false;
  }
}

// -- claims ------------------------------------------------------------------

export const evidenceRefSchema = z
  .object({
    artifact_id: z.string().nullish(),
    url: z.string().nullish(),
    kind: z.string().nullish(),
    detail: z.string().nullish(),
    role: z.string().nullish(),
  })
  .passthrough();
export type EvidenceRef = z.infer<typeof evidenceRefSchema>;

export const claimSchema = z
  .object({
    claim_id: z.string(),
    text: z.string(),
    claim_type: z.string(),
    subject: z.string().nullish(),
    evidence: z.array(evidenceRefSchema).default([]),
    evidence_count: z.number().nullish(),
    evidence_is_methodological: z.boolean().default(false),
    derivation: z.string().nullish(),
    confidence,
  })
  .passthrough();
export type Claim = z.infer<typeof claimSchema>;

export const claimsFileSchema = z
  .object({
    claims: z.array(claimSchema),
    count: z.number().nullish(),
    correction_pathway: z
      .object({
        enabled: z.boolean().default(false),
        instructions: z.string().nullish(),
        contact_field: z.string().nullish(),
      })
      .passthrough()
      .nullish(),
  })
  .passthrough();

// -- manifest ----------------------------------------------------------------

export const manifestSchema = z
  .object({
    manifest_version: z.string(),
    generated_at: z.string(),
    methodology_version: z.string(),
    title: z.string(),
    subtitle: z.string().nullish(),
    fixture: z.boolean().optional(),
    fixture_note: z.string().nullish(),
    source: z
      .object({
        repository_url: z.string(),
        analyzed_head_sha: z.string(),
        is_shallow_clone: z.boolean().nullish(),
      })
      .passthrough(),
    window: z
      .object({
        start: z.string(),
        end: z.string(),
        lookback_days: z.number().nullish(),
      })
      .passthrough(),
    phase1_provenance: z.record(z.unknown()).nullish(),
    counts: z.record(z.number()).default({}),
    files: z
      .record(
        z
          .object({
            path: z.string(),
            bytes: z.number().nullish(),
            sha256: z.string().nullish(),
            rows: z.number().nullish(),
          })
          .passthrough(),
      )
      .default({}),
    indexes: z.record(z.unknown()).nullish(),
    validation_status: z.string(),
    publishable: z.boolean(),
    publishable_blockers: z
      .array(
        z
          .object({
            item: z.string(),
            status: z.string().nullish(),
            queue_file: z.string().nullish(),
          })
          .passthrough(),
      )
      .default([]),
    safety_scan: z
      .object({ status: z.string(), violations: z.array(z.unknown()).default([]) })
      .passthrough()
      .nullish(),
    limitations_headline: z.string(),
    ui_contract: z.record(z.unknown()).nullish(),
  })
  .passthrough();
export type Manifest = z.infer<typeof manifestSchema>;

// -- rankings ----------------------------------------------------------------

/**
 * A closed interval, or nothing.
 *
 * Documented as `[number, number]`, but Phase 2 emits `[null, null]` for a
 * dimension it could not assess. Both are accepted and `intervalOf()` collapses
 * the null form to absent, so the UI never renders half an interval or mistakes
 * a null bound for a zero one. Recorded as a contract deviation in
 * CURRENT_STATE.md.
 */
export const intervalSchema = z.tuple([z.number().nullable(), z.number().nullable()]).nullish();

export type Interval = [number, number] | null;

/** Normalise any published interval to a usable pair, or null. */
export function intervalOf(raw: unknown): Interval {
  if (!Array.isArray(raw) || raw.length !== 2) return null;
  const [lo, hi] = raw;
  if (typeof lo !== 'number' || typeof hi !== 'number') return null;
  if (Number.isNaN(lo) || Number.isNaN(hi)) return null;
  return [lo, hi];
}

export const stabilitySchema = z
  .object({
    rank_stability_index: z.number().nullish(),
    top5_inclusion_probability: z.number().nullish(),
    position_range: intervalSchema,
  })
  .passthrough();
export type Stability = z.infer<typeof stabilitySchema>;

export const positionSchema = z
  .object({
    position: z.number(),
    tier: z.number(),
    actor_cluster_id: z.string(),
    login: z.string().nullish(),
    dimension_values: z.record(z.number().nullable()).default({}),
    incomparable_with: z.array(z.string()).default([]),
    incomparable_count: z.number().nullish(),
    cross_check_position: z.number().nullish(),
    cross_check_delta: z.number().nullish(),
    stability: stabilitySchema.nullish(),
  })
  .passthrough();
export type Position = z.infer<typeof positionSchema>;

export const scenarioSchema = z
  .object({
    scenario: z.string(),
    label: z.string().nullish(),
    description: z.string().nullish(),
    available: z.boolean(),
    unavailable_reason: z.string().nullish(),
    remedy: z.string().nullish(),
    note: z.string().nullish(),
    weights: z.record(z.number()).nullish(),
    thresholds: z.record(z.record(z.number())).nullish(),
    alternatives: z.number().nullish(),
    excluded_insufficient_evidence: z.number().nullish(),
    positions: z.array(positionSchema).default([]),
    cross_check: z.record(z.unknown()).nullish(),
  })
  .passthrough();
export type Scenario = z.infer<typeof scenarioSchema>;

export const rankingsSchema = z
  .object({
    default_scenario: z.string(),
    scenarios: z.array(scenarioSchema),
    method: z
      .object({
        name: z.string().nullish(),
        cross_check: z.string().nullish(),
        why_not_a_score: z.string().nullish(),
        tiers_explained: z.string().nullish(),
      })
      .passthrough()
      .nullish(),
  })
  .passthrough();
export type Rankings = z.infer<typeof rankingsSchema>;

// -- engineers ---------------------------------------------------------------

export const dimensionProfileSchema = z
  .object({
    dimension: z.string(),
    value: z.number().nullable(),
    interval: intervalSchema,
    confidence,
    is_unknown: z.boolean().default(false),
    unknown_reason: z.string().nullish(),
    top_episode_id: z.string().nullish(),
    episode_count: z.number().nullish(),
    aggregation_trace: z
      .array(
        z
          .object({
            rank: z.number(),
            value: z.number(),
            coefficient: z.number(),
            contribution: z.number(),
            headroom_capped: z.boolean().nullish(),
          })
          .passthrough(),
      )
      .nullish(),
  })
  .passthrough();
export type DimensionProfile = z.infer<typeof dimensionProfileSchema>;

export const engineerSchema = z
  .object({
    actor_cluster_id: z.string(),
    login: z.string().nullish(),
    display_name: z.string().nullish(),
    profile_url: z.string().nullish(),
    avatar_url: z.string().nullish(),
    affiliation: z.string().nullish(),
    affiliation_note: z.string().nullish(),
    identity_ambiguity: z.string().nullish(),
    identity_ambiguity_reasons: z.array(z.string()).nullish(),
    portfolio_id: z.string().nullish(),
    thesis_claim_ids: z.array(z.string()).default([]),
    dimension_profile: z.array(dimensionProfileSchema).default([]),
    strongest_dimension: z.string().nullish(),
    strongest_evidence_episode_id: z.string().nullish(),
    episode_ids: z.array(z.string()).default([]),
    episode_count: z.number().nullish(),
    current_episode_ids: z.array(z.string()).default([]),
    foundational_episode_ids: z.array(z.string()).default([]),
    roles_held: z.array(z.string()).default([]),
    concentration_profile: z.string().nullish(),
    diversity_affects_ranking: z.boolean().nullish(),
    active_period: z
      .object({
        first_observed: z.string().nullish(),
        last_observed: z.string().nullish(),
        span_days: z.number().nullish(),
        note: z.string().nullish(),
      })
      .passthrough()
      .nullish(),
    rankable: z.boolean(),
    eligibility_label: z.string().nullish(),
    eligibility_reasons: z.array(z.string()).default([]),
    uncertainty: stabilitySchema.extend({ claim_id: z.string().nullish() }).nullish(),
  })
  .passthrough();
export type Engineer = z.infer<typeof engineerSchema>;

// -- episodes ----------------------------------------------------------------

export const episodeDimensionSchema = z
  .object({
    dimension: z.string(),
    band: z.number().nullable(),
    band_label: z.string().nullish(),
    is_unknown: z.boolean().default(false),
    unknown_reason: z.string().nullish(),
    confidence,
    confidence_reasons: z.array(z.string()).default([]),
    corroboration_status: z.string().nullish(),
    artifact_classes: z.array(z.string()).default([]),
    evidence: z.array(evidenceRefSchema).default([]),
    counterevidence: z.array(z.unknown()).default([]),
    rationale_claim_id: z.string().nullish(),
  })
  .passthrough();
export type EpisodeDimension = z.infer<typeof episodeDimensionSchema>;

export const participantSchema = z
  .object({
    actor_cluster_id: z.string(),
    login: z.string().nullish(),
    roles: z.array(z.string()).default([]),
    share_category: z.string().nullish(),
    share_reasons: z.array(z.string()).default([]),
    attribution_confidence: confidence,
    direct_evidence: z.array(evidenceRefSchema).default([]),
    claim_ids: z.array(z.string()).default([]),
  })
  .passthrough();
export type Participant = z.infer<typeof participantSchema>;

export const counterevidenceSchema = z
  .object({
    kind: z.string().nullish(),
    evidence_tier: z.string().nullish(),
    requires_human_confirmation: z.boolean().default(false),
    detail: z.string().nullish(),
    pr_number: z.number().nullish(),
  })
  .passthrough();
export type Counterevidence = z.infer<typeof counterevidenceSchema>;

export const propagationSchema = z
  .object({
    reach_file_count: z.number().nullish(),
    reach_pr_count: z.number().nullish(),
    distinct_component_penetration: z.number().nullish(),
    components_reached: z.array(z.string()).nullish(),
    distinct_downstream_authors: z.number().nullish(),
    max_path_depth: z.number().nullish(),
    mass_after_cap: z.number().nullish(),
    cap_applied: z.boolean().nullish(),
    source_age_days: z.number().nullish(),
    raw_decay_factor: z.number().nullish(),
    persistence_detected: z.boolean().nullish(),
    effective_decay_factor: z.number().nullish(),
    reason: z.string().nullish(),
    walk_truncated: z.boolean().nullish(),
  })
  .passthrough();
export type Propagation = z.infer<typeof propagationSchema>;

export const episodeSchema = z
  .object({
    episode_id: z.string(),
    title_claim_id: z.string().nullish(),
    problem_claim_id: z.string().nullish(),
    intervention_claim_id: z.string().nullish(),
    outcome_claim_id: z.string().nullish(),
    title: z.string().nullish(),
    started_at: z.string().nullish(),
    ended_at: z.string().nullish(),
    duration_days: z.number().nullish(),
    status: z.string(),
    status_reasons: z.array(z.string()).default([]),
    release_corroboration: z.string().nullish(),
    release_evidence: z.array(evidenceRefSchema).default([]),
    components: z.array(z.string()).default([]),
    products: z.array(z.string()).default([]),
    reachability_band: z.string().nullish(),
    feature_flag_keys: z.array(z.string()).default([]),
    pr_numbers: z.array(z.number()).default([]),
    issue_numbers: z.array(z.number()).default([]),
    cluster_confidence: z.number().nullish(),
    cluster_confidence_reasons: z.array(z.string()).default([]),
    sub_episode_links: z.array(z.record(z.unknown())).default([]),
    counterevidence: z.array(counterevidenceSchema).default([]),
    has_ai_co_author: z.boolean().nullish(),
    touches_enterprise_licensed_code: z.boolean().nullish(),
    dimensions: z.array(episodeDimensionSchema).default([]),
    participants: z.array(participantSchema).default([]),
    artifact_ids: z.array(z.string()).default([]),
    analytics: z
      .object({
        propagation: propagationSchema.nullish(),
        novelty: z
          .object({
            novelty_class: z.string().nullish(),
            rationale: z.string().nullish(),
            markers: z.array(z.string()).nullish(),
            uncertainty: z.array(z.string()).nullish(),
          })
          .passthrough()
          .nullish(),
        corrective_burden: z
          .object({
            by_class: z.record(z.number()).nullish(),
            capped_penalty: z.number().nullish(),
            confirmed_revert: z.boolean().nullish(),
            unconfirmed_event_count: z.number().nullish(),
          })
          .passthrough()
          .nullish(),
      })
      .passthrough()
      .nullish(),
  })
  .passthrough();
export type Episode = z.infer<typeof episodeSchema>;

// -- comparisons -------------------------------------------------------------

export const pairwiseSchema = z
  .object({
    a: z.string(),
    b: z.string(),
    a_login: z.string().nullish(),
    b_login: z.string().nullish(),
    concordance: z.number().nullish(),
    credibility: z.number().nullish(),
    per_criterion: z
      .array(
        z
          .object({
            criterion: z.string(),
            a_value: z.number().nullable(),
            b_value: z.number().nullable(),
            difference: z.number().nullish(),
            weight: z.number().nullish(),
            concordance: z.number().nullish(),
            discordance: z.number().nullish(),
            thresholds: z.record(z.number()).nullish(),
          })
          .passthrough(),
      )
      .default([]),
    excluded_criteria: z
      .array(
        z
          .object({
            criterion: z.string(),
            reason: z.string().nullish(),
            a_unknown_reason: z.string().nullish(),
            b_unknown_reason: z.string().nullish(),
          })
          .passthrough(),
      )
      .default([]),
    vetoing_criteria: z.array(z.unknown()).default([]),
    counterevidence_veto: z.boolean().nullish(),
    explanation_claim_id: z.string().nullish(),
  })
  .passthrough();
export type Pairwise = z.infer<typeof pairwiseSchema>;

export const comparisonsSchema = z
  .object({
    scenarios: z.record(
      z
        .object({
          top_five: z.array(z.string()).default([]),
          pairwise: z.array(pairwiseSchema).default([]),
          methodology_trace: z.string().nullish(),
        })
        .passthrough(),
    ),
  })
  .passthrough();
export type Comparisons = z.infer<typeof comparisonsSchema>;

// -- coverage / methodology / indexes / evidence -----------------------------

export const coverageSchema = z
  .object({
    phase1: z.record(z.unknown()).nullish(),
    known_gaps: z
      .array(
        z
          .object({
            gap: z.string(),
            detail: z.string().nullish(),
            consequence: z.string().nullish(),
            severity: z.string().nullish(),
          })
          .passthrough(),
      )
      .default([]),
    capabilities_disabled: z.record(z.string()).default({}),
    summaries: z.record(z.unknown()).nullish(),
    validation: z
      .object({
        status: z.string().nullish(),
        publishable: z.boolean().nullish(),
        publishable_blockers: z.array(z.record(z.unknown())).default([]),
        items: z.array(z.record(z.unknown())).default([]),
      })
      .passthrough()
      .nullish(),
    limitations: z
      .object({
        headline: z.string(),
        items: z.array(z.string()).default([]),
        claim_ids: z.array(z.string()).default([]),
        correction_pathway: z.record(z.unknown()).nullish(),
      })
      .passthrough(),
    missingness: z
      .object({
        dimension_unknown_rates: z
          .record(
            z
              .object({
                assessed: z.number().nullish(),
                unknown: z.number().nullish(),
                unknown_rate: z.number().nullish(),
              })
              .passthrough(),
          )
          .default({}),
        episodes_without_diff: z.number().nullish(),
        episodes_without_release_corroboration: z.number().nullish(),
        engineers_below_evidence_bar: z.number().nullish(),
        note: z.string().nullish(),
      })
      .passthrough()
      .nullish(),
  })
  .passthrough();
export type Coverage = z.infer<typeof coverageSchema>;

export const methodologySchema = z
  .object({
    methodology_version: z.string(),
    export_schema_version: z.string().nullish(),
    impact_definition: z.string(),
    unit_of_analysis: z.string(),
    formulas: z.record(z.string()).default({}),
    explicitly_not_used: z.array(z.string()).default([]),
    llm: z
      .object({
        provider: z.string().nullish(),
        model: z.string().nullish(),
        available: z.boolean(),
        role: z.string().nullish(),
        usage: z.unknown().nullish(),
        note: z.string().nullish(),
      })
      .passthrough(),
    rubric: z.unknown().nullish(),
    attribution: z.unknown().nullish(),
    outranking: z.unknown().nullish(),
    analytics: z.unknown().nullish(),
    episode_construction: z.unknown().nullish(),
    eligibility: z.unknown().nullish(),
  })
  .passthrough();
export type Methodology = z.infer<typeof methodologySchema>;

export const indexesSchema = z
  .object({
    episodes_by_component: z.record(z.array(z.string())).default({}),
    episodes_by_status: z.record(z.array(z.string())).default({}),
    episodes_by_engineer: z.record(z.array(z.string())).default({}),
    engineers_by_role: z.record(z.array(z.string())).default({}),
    engineers_by_strongest_dimension: z.record(z.array(z.string())).default({}),
  })
  .passthrough();
export type Indexes = z.infer<typeof indexesSchema>;

export const artifactSchema = z
  .object({
    artifact_id: z.string(),
    kind: z.string().nullish(),
    url: z.string().nullish(),
    title: z.string().nullish(),
    detail: z.string().nullish(),
    provenance: z.string().nullish(),
  })
  .passthrough();
export type Artifact = z.infer<typeof artifactSchema>;

export const evidenceIndexSchema = z
  .object({
    sharded: z.boolean().nullish(),
    total_artifacts: z.number().nullish(),
    note: z.string().nullish(),
    shards: z.record(z.object({ file: z.string(), count: z.number().nullish() }).passthrough()).default({}),
  })
  .passthrough();
