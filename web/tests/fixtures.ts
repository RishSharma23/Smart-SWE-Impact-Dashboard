import type { Claim, DimensionProfile, Episode, Pairwise, Participant } from '@/lib/schema';
import type { EpisodeSummary, LeaderView, PairwiseView } from '@/lib/viewmodel';

export const claim = (over: Partial<Claim> = {}): Claim => ({
  claim_id: 'claim/abc123',
  text: 'The change landed on the default branch.',
  claim_type: 'episode_narrative',
  subject: 'ep/1',
  evidence: [
    {
      artifact_id: 'github.com/PostHog/posthog#pr/1',
      url: 'https://github.com/PostHog/posthog/pull/1',
      kind: 'pull_request',
      detail: 'state=MERGED',
    },
  ],
  evidence_count: 1,
  evidence_is_methodological: false,
  derivation: 'episodes.status:classify',
  confidence: 'high',
  ...over,
});

export const dimension = (over: Partial<DimensionProfile> = {}): DimensionProfile => ({
  dimension: 'product_outcome',
  value: 3.15,
  interval: [2.6, 3.6],
  confidence: 'high',
  is_unknown: false,
  unknown_reason: null,
  top_episode_id: 'ep/1',
  episode_count: 4,
  aggregation_trace: [{ rank: 1, value: 3.0, coefficient: 1.0, contribution: 3.0, headroom_capped: false }],
  ...over,
});

export const unknownDimension = (dim = 'collaborative_amplification'): DimensionProfile =>
  dimension({
    dimension: dim,
    value: null,
    interval: null,
    confidence: 'unknown',
    is_unknown: true,
    unknown_reason: 'review_intervention_candidates is absent, so this cannot be assessed',
    aggregation_trace: null,
  });

export const episodeSummary = (over: Partial<EpisodeSummary> = {}): EpisodeSummary => ({
  episodeId: 'github.com/PostHog/posthog#episode/1-aaaa',
  slug: '1-aaaa',
  title: 'Session replay export pipeline',
  titleClaim: null,
  status: 'shipped_observable',
  releaseCorroboration: 'corroborated',
  startedAt: '2026-06-02T09:14:00Z',
  endedAt: '2026-06-19T16:02:00Z',
  components: ['product:replay'],
  prNumbers: [40101],
  counterevidenceCount: 0,
  requiresHumanConfirmation: false,
  reachabilityBand: 'cross_product',
  shareCategory: 'primary',
  roles: ['core_implementer'],
  participantCount: 2,
  ...over,
});

export const leader = (over: Partial<LeaderView> = {}): LeaderView => ({
  actorClusterId: 'github/user/ada',
  slug: 'ada',
  login: 'ada',
  displayName: 'Ada',
  avatarUrl: 'https://github.com/ada.png',
  profileUrl: 'https://github.com/ada',
  affiliationNote: 'Affiliation is not asserted.',
  position: 1,
  tier: 1,
  sharedTierWith: ['grace'],
  incomparableWith: ['grace'],
  crossCheckDelta: 1,
  crossCheckPosition: 2,
  stability: { rank_stability_index: 0.86, top5_inclusion_probability: 0.94, position_range: [1, 3] },
  dimensionValues: { product_outcome: 3.15, collaborative_amplification: null },
  dimensionProfile: [dimension(), unknownDimension()],
  strongestDimension: 'product_outcome',
  thesisClaims: [claim({ claim_id: 'claim/thesis', claim_type: 'portfolio' })],
  topEpisodes: [episodeSummary()],
  rolesHeld: ['core_implementer'],
  concentrationProfile: 'few_episodes',
  episodeCount: 4,
  identityAmbiguity: 'resolved',
  identityAmbiguityReasons: [],
  ...over,
});

export const participant = (over: Partial<Participant> = {}): Participant => ({
  actor_cluster_id: 'github/user/ada',
  login: 'ada',
  roles: ['core_implementer'],
  share_category: 'primary',
  share_reasons: ['sole core implementer of the episode'],
  attribution_confidence: 'high',
  direct_evidence: [
    {
      artifact_id: 'github.com/PostHog/posthog#pr/1',
      url: 'https://github.com/PostHog/posthog/pull/1',
      kind: 'pull_request',
      detail: 'authored PR #1',
      role: 'core_implementer',
    },
  ],
  claim_ids: [],
  ...over,
});

export const pairwise = (over: Partial<Pairwise> = {}): PairwiseView => ({
  a: 'github/user/ada',
  b: 'github/user/grace',
  a_login: 'ada',
  b_login: 'grace',
  concordance: 0.636,
  credibility: 0.636,
  per_criterion: [
    {
      criterion: 'product_outcome',
      a_value: 3.15,
      b_value: 2.4,
      difference: 0.75,
      weight: 0.22,
      concordance: 1,
      discordance: 0,
      thresholds: { q: 0, p: 1, v: 3 },
    },
    {
      criterion: 'engineering_leverage',
      a_value: null,
      b_value: 1.2,
      difference: null,
      weight: 0.2,
      concordance: null,
      discordance: null,
      thresholds: { q: 0, p: 1, v: 3 },
    },
  ],
  excluded_criteria: [
    {
      criterion: 'engineering_leverage',
      reason: 'unknown for ada',
      a_unknown_reason: 'no import parser for Rust',
      b_unknown_reason: null,
    },
  ],
  vetoing_criteria: [],
  counterevidence_veto: false,
  explanation_claim_id: 'claim/why',
  explanationClaim: claim({ claim_id: 'claim/why', claim_type: 'ranking', text: 'ada ranks above grace because …' }),
  ...over,
} as PairwiseView);
