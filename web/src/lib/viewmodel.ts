import 'server-only';

import { featuredEpisodeIds, renderedEpisodeIds, slugFor, type Bundle } from './data';
import type { Claim, DimensionProfile, Engineer, Episode, Pairwise, Position, Scenario, Stability } from './schema';

/**
 * Server-side view models. Pages are statically generated, so these decide
 * exactly how much of the (potentially very large) export is inlined into each
 * HTML file. Nothing here invents a value: every field traces to the package.
 */

export interface EpisodeSummary {
  episodeId: string;
  slug: string | null;
  title: string | null;
  titleClaim: Claim | null;
  status: string;
  releaseCorroboration: string | null;
  startedAt: string | null;
  endedAt: string | null;
  components: string[];
  prNumbers: number[];
  counterevidenceCount: number;
  requiresHumanConfirmation: boolean;
  reachabilityBand: string | null;
  shareCategory: string | null;
  roles: string[];
  participantCount: number;
}

export interface LeaderView {
  actorClusterId: string;
  slug: string;
  login: string;
  displayName: string;
  avatarUrl: string | null;
  profileUrl: string | null;
  affiliationNote: string | null;
  position: number;
  tier: number;
  sharedTierWith: string[];
  incomparableWith: string[];
  crossCheckDelta: number | null;
  crossCheckPosition: number | null;
  stability: Stability | null;
  dimensionValues: Record<string, number | null>;
  dimensionProfile: DimensionProfile[];
  strongestDimension: string | null;
  thesisClaims: Claim[];
  topEpisodes: EpisodeSummary[];
  rolesHeld: string[];
  concentrationProfile: string | null;
  episodeCount: number;
  identityAmbiguity: string | null;
  identityAmbiguityReasons: string[];
}

export interface PairwiseView extends Pairwise {
  explanationClaim: Claim | null;
}

export function episodeSummary(bundle: Bundle, episodeId: string, forActor?: string): EpisodeSummary | null {
  const ep = bundle.episodesById.get(episodeId);
  if (!ep) return null;
  const participant = forActor ? ep.participants.find((p) => p.actor_cluster_id === forActor) : undefined;
  return {
    episodeId: ep.episode_id,
    slug: bundle.episodePageIds.has(ep.episode_id) ? slugFor(ep.episode_id) : null,
    title: ep.title ?? null,
    titleClaim: ep.title_claim_id ? (bundle.claimsById.get(ep.title_claim_id) ?? null) : null,
    status: ep.status,
    releaseCorroboration: ep.release_corroboration ?? null,
    startedAt: ep.started_at ?? null,
    endedAt: ep.ended_at ?? null,
    components: ep.components.slice(0, 4),
    prNumbers: ep.pr_numbers.slice(0, 6),
    counterevidenceCount: ep.counterevidence.length,
    requiresHumanConfirmation: ep.counterevidence.some((c) => c.requires_human_confirmation),
    reachabilityBand: ep.reachability_band ?? null,
    shareCategory: participant?.share_category ?? null,
    roles: participant?.roles ?? [],
    participantCount: ep.participants.length,
  };
}

export function claimsFor(bundle: Bundle, ids: (string | null | undefined)[]): Claim[] {
  const out: Claim[] = [];
  for (const id of ids) {
    if (!id) continue;
    const c = bundle.claimsById.get(id);
    if (c && !out.some((x) => x.claim_id === c.claim_id)) out.push(c);
  }
  return out;
}

export function leaderView(
  bundle: Bundle,
  scenario: Scenario,
  position: Position,
  { episodeLimit = 3 }: { episodeLimit?: number } = {},
): LeaderView | null {
  const engineer = bundle.engineersById.get(position.actor_cluster_id);
  if (!engineer) return null;

  const sharedTierWith = scenario.positions
    .filter((p) => p.tier === position.tier && p.actor_cluster_id !== position.actor_cluster_id)
    .map((p) => p.login ?? p.actor_cluster_id);

  const incomparableWith = position.incomparable_with
    .map((id) => bundle.engineersById.get(id)?.login ?? id)
    .filter(Boolean);

  const topEpisodes = featuredEpisodeIds(engineer)
    .slice(0, episodeLimit)
    .map((id) => episodeSummary(bundle, id, engineer.actor_cluster_id))
    .filter((e): e is EpisodeSummary => Boolean(e));

  return {
    actorClusterId: engineer.actor_cluster_id,
    slug: slugFor(engineer.actor_cluster_id),
    login: engineer.login ?? engineer.actor_cluster_id,
    displayName: engineer.display_name ?? engineer.login ?? engineer.actor_cluster_id,
    avatarUrl: engineer.avatar_url ?? null,
    profileUrl: engineer.profile_url ?? null,
    affiliationNote: engineer.affiliation_note ?? null,
    position: position.position,
    tier: position.tier,
    sharedTierWith,
    incomparableWith,
    crossCheckDelta: position.cross_check_delta ?? null,
    crossCheckPosition: position.cross_check_position ?? null,
    stability: position.stability ?? engineer.uncertainty ?? null,
    dimensionValues: position.dimension_values,
    dimensionProfile: engineer.dimension_profile,
    strongestDimension: engineer.strongest_dimension ?? null,
    thesisClaims: claimsFor(bundle, engineer.thesis_claim_ids).slice(0, 3),
    topEpisodes,
    rolesHeld: engineer.roles_held,
    concentrationProfile: engineer.concentration_profile ?? null,
    episodeCount: engineer.episode_count ?? engineer.episode_ids.length,
    identityAmbiguity: engineer.identity_ambiguity ?? null,
    identityAmbiguityReasons: engineer.identity_ambiguity_reasons ?? [],
  };
}

/** Top five (or fewer) for every scenario, keyed by scenario name. */
export function leadersByScenario(bundle: Bundle): Record<string, LeaderView[]> {
  const out: Record<string, LeaderView[]> = {};
  for (const scenario of bundle.rankings.scenarios) {
    out[scenario.scenario] = scenario.positions
      .slice()
      .sort((a, b) => a.position - b.position)
      .slice(0, 5)
      .map((p) => leaderView(bundle, scenario, p))
      .filter((v): v is LeaderView => Boolean(v));
  }
  return out;
}

/** Pairwise material for a scenario, with the explanation claim resolved. */
export function pairwiseByScenario(bundle: Bundle): Record<string, PairwiseView[]> {
  const out: Record<string, PairwiseView[]> = {};
  for (const [name, block] of Object.entries(bundle.comparisons.scenarios)) {
    out[name] = block.pairwise.map((p) => ({
      ...p,
      explanationClaim: p.explanation_claim_id ? (bundle.claimsById.get(p.explanation_claim_id) ?? null) : null,
    }));
  }
  return out;
}

/** Where the same names appear across scenarios — the "do positions move?" view. */
export interface ScenarioMovement {
  actorClusterId: string;
  login: string;
  slug: string;
  byScenario: Record<string, { position: number; tier: number } | null>;
  appearsIn: number;
  moves: boolean;
}

export function scenarioMovement(bundle: Bundle): { scenarios: string[]; rows: ScenarioMovement[] } {
  const available = bundle.rankings.scenarios.filter((s) => s.available && s.positions.length > 0);
  const seen = new Map<string, ScenarioMovement>();

  for (const scenario of available) {
    for (const p of scenario.positions.slice().sort((a, b) => a.position - b.position).slice(0, 5)) {
      const engineer = bundle.engineersById.get(p.actor_cluster_id);
      if (!engineer) continue;
      let row = seen.get(p.actor_cluster_id);
      if (!row) {
        row = {
          actorClusterId: p.actor_cluster_id,
          login: engineer.login ?? p.actor_cluster_id,
          slug: slugFor(p.actor_cluster_id),
          byScenario: Object.fromEntries(available.map((s) => [s.scenario, null])),
          appearsIn: 0,
          moves: false,
        };
        seen.set(p.actor_cluster_id, row);
      }
      row.byScenario[scenario.scenario] = { position: p.position, tier: p.tier };
      row.appearsIn += 1;
    }
  }

  const rows = [...seen.values()];
  for (const row of rows) {
    const positions = Object.values(row.byScenario)
      .filter(Boolean)
      .map((v) => v!.position);
    row.moves = row.appearsIn !== available.length || new Set(positions).size > 1;
  }
  rows.sort((a, b) => {
    const first = available[0]?.scenario;
    const ap = first ? (a.byScenario[first]?.position ?? 99) : 99;
    const bp = first ? (b.byScenario[first]?.position ?? 99) : 99;
    return ap - bp || a.login.localeCompare(b.login);
  });

  return { scenarios: available.map((s) => s.scenario), rows };
}

/** Episode + everything needed to render its detail page. */
export interface EpisodeView {
  episode: Episode;
  claims: {
    title: Claim | null;
    problem: Claim | null;
    intervention: Claim | null;
    outcome: Claim | null;
  };
  dimensionClaims: Record<string, Claim | null>;
  participantClaims: Record<string, Claim[]>;
  artifacts: { artifact_id: string; kind: string | null; url: string | null; title: string | null; detail: string | null }[];
  engineerSlugs: Record<string, string>;
}

export function episodeView(bundle: Bundle, episodeId: string): EpisodeView | null {
  const episode = bundle.episodesById.get(episodeId);
  if (!episode) return null;

  const dimensionClaims: Record<string, Claim | null> = {};
  for (const d of episode.dimensions) {
    dimensionClaims[d.dimension] = d.rationale_claim_id ? (bundle.claimsById.get(d.rationale_claim_id) ?? null) : null;
  }

  const participantClaims: Record<string, Claim[]> = {};
  const engineerSlugs: Record<string, string> = {};
  for (const p of episode.participants) {
    participantClaims[p.actor_cluster_id] = claimsFor(bundle, p.claim_ids);
    if (bundle.engineersById.has(p.actor_cluster_id)) {
      engineerSlugs[p.actor_cluster_id] = slugFor(p.actor_cluster_id);
    }
  }

  const artifacts = episode.artifact_ids
    .map((id) => bundle.artifactsById.get(id))
    .filter(Boolean)
    .map((a) => ({
      artifact_id: a!.artifact_id,
      kind: a!.kind ?? null,
      url: a!.url ?? null,
      title: a!.title ?? null,
      detail: a!.detail ?? null,
    }));

  return {
    episode,
    claims: {
      title: episode.title_claim_id ? (bundle.claimsById.get(episode.title_claim_id) ?? null) : null,
      problem: episode.problem_claim_id ? (bundle.claimsById.get(episode.problem_claim_id) ?? null) : null,
      intervention: episode.intervention_claim_id ? (bundle.claimsById.get(episode.intervention_claim_id) ?? null) : null,
      outcome: episode.outcome_claim_id ? (bundle.claimsById.get(episode.outcome_claim_id) ?? null) : null,
    },
    dimensionClaims,
    participantClaims,
    artifacts,
    engineerSlugs,
  };
}

/** Everything an engineer detail page renders. */
export interface EngineerView {
  engineer: Engineer;
  slug: string;
  thesisClaims: Claim[];
  stabilityClaim: Claim | null;
  positions: {
    scenario: string;
    label: string;
    available: boolean;
    position: number | null;
    tier: number | null;
    crossCheckDelta: number | null;
    incomparableWith: string[];
    stability: Stability | null;
    unavailableReason: string | null;
  }[];
  featured: EpisodeSummary[];
  current: EpisodeSummary[];
  foundational: EpisodeSummary[];
  otherEpisodes: EpisodeSummary[];
  reviewInterventions: {
    episodeId: string;
    slug: string | null;
    title: string | null;
    detail: string | null;
    url: string | null;
    role: string | null;
  }[];
  counterevidence: {
    episodeId: string;
    slug: string | null;
    episodeTitle: string | null;
    kind: string | null;
    detail: string | null;
    evidenceTier: string | null;
    requiresHumanConfirmation: boolean;
    prNumber: number | null;
  }[];
  collaborators: { actorClusterId: string; login: string; slug: string | null; sharedEpisodes: number; roles: string[] }[];
  propagation: {
    episodeId: string;
    slug: string | null;
    title: string | null;
    reachFileCount: number | null;
    reachPrCount: number | null;
    componentsReached: string[];
    distinctDownstreamAuthors: number | null;
    maxPathDepth: number | null;
    persistenceDetected: boolean | null;
    effectiveDecayFactor: number | null;
    sourceAgeDays: number | null;
    capApplied: boolean | null;
    walkTruncated: boolean | null;
    reason: string | null;
  }[];
  episodePagesTruncated: boolean;
}

/**
 * Per-page caps. Without these a prolific contributor's page inlines every
 * claim on every episode they touched, and one profile measured 20 MB before
 * these limits existed. The page still links to everything; it just does not
 * embed it.
 *
 * The numbers come from `render_plan` in the manifest, because Phase 2 shipped
 * exactly the episodes these caps can display. A cap raised here without being
 * raised there would ask for records the package does not carry.
 */
const THESIS_LIMIT = 8;

export function engineerView(bundle: Bundle, actorClusterId: string): EngineerView | null {
  const engineer = bundle.engineersById.get(actorClusterId);
  if (!engineer) return null;

  const budget = bundle.renderPlan.budget;
  const featuredIds = featuredEpisodeIds(engineer);
  const featuredSet = new Set(featuredIds);

  const toSummary = (id: string) => episodeSummary(bundle, id, actorClusterId);
  const featured = featuredIds
    .slice(0, budget.featured)
    .map(toSummary)
    .filter((e): e is EpisodeSummary => Boolean(e));
  const current = engineer.current_episode_ids
    .slice(0, budget.current)
    .map(toSummary)
    .filter((e): e is EpisodeSummary => Boolean(e));
  const foundational = engineer.foundational_episode_ids
    .slice(0, budget.foundational)
    .map(toSummary)
    .filter((e): e is EpisodeSummary => Boolean(e));

  const otherIds = engineer.episode_ids.filter((id) => !featuredSet.has(id));
  const otherEpisodes = otherIds
    .slice(0, budget.other)
    .map(toSummary)
    .filter((e): e is EpisodeSummary => Boolean(e))
    .sort((a, b) => (b.startedAt ?? '').localeCompare(a.startedAt ?? ''));

  const positions = bundle.rankings.scenarios.map((s) => {
    const p = s.positions.find((x) => x.actor_cluster_id === actorClusterId);
    return {
      scenario: s.scenario,
      label: s.label ?? s.scenario,
      available: s.available,
      position: p?.position ?? null,
      tier: p?.tier ?? null,
      crossCheckDelta: p?.cross_check_delta ?? null,
      incomparableWith: (p?.incomparable_with ?? []).map((id) => bundle.engineersById.get(id)?.login ?? id),
      stability: p?.stability ?? null,
      unavailableReason: s.unavailable_reason ?? null,
    };
  });

  // Review interventions, counterevidence, collaborators and propagation are all
  // read off the engineer's own episodes — no new inference happens here.
  const reviewInterventions: EngineerView['reviewInterventions'] = [];
  const counterevidence: EngineerView['counterevidence'] = [];
  const collaboratorMap = new Map<string, { login: string; sharedEpisodes: number; roles: Set<string> }>();
  const propagation: EngineerView['propagation'] = [];

  // These panels read the episodes this profile actually puts on the page.
  // Scanning further would be slow, would be cut by the caps below anyway, and
  // would ask for episodes a projected package does not carry, which would make
  // the same profile render differently from two packages of the same run.
  for (const id of renderedEpisodeIds(engineer, budget)) {
    const ep = bundle.episodesById.get(id);
    if (!ep) continue;
    const slug = bundle.episodePageIds.has(id) ? slugFor(id) : null;
    const me = ep.participants.find((p) => p.actor_cluster_id === actorClusterId);

    for (const ev of me?.direct_evidence ?? []) {
      if (ev.kind === 'review_comment' || ev.role === 'risk_preventer' || ev.role === 'decision_shaper') {
        reviewInterventions.push({
          episodeId: id,
          slug,
          title: ep.title ?? null,
          detail: ev.detail ?? null,
          url: ev.url ?? null,
          role: ev.role ?? null,
        });
      }
    }

    for (const c of ep.counterevidence) {
      counterevidence.push({
        episodeId: id,
        slug,
        episodeTitle: ep.title ?? null,
        kind: c.kind ?? null,
        detail: c.detail ?? null,
        evidenceTier: c.evidence_tier ?? null,
        requiresHumanConfirmation: c.requires_human_confirmation,
        prNumber: c.pr_number ?? null,
      });
    }

    for (const p of ep.participants) {
      if (p.actor_cluster_id === actorClusterId) continue;
      const entry = collaboratorMap.get(p.actor_cluster_id) ?? {
        login: p.login ?? p.actor_cluster_id,
        sharedEpisodes: 0,
        roles: new Set<string>(),
      };
      entry.sharedEpisodes += 1;
      p.roles.forEach((r) => entry.roles.add(r));
      collaboratorMap.set(p.actor_cluster_id, entry);
    }

    const prop = ep.analytics?.propagation;
    if (prop && featuredSet.has(id)) {
      propagation.push({
        episodeId: id,
        slug,
        title: ep.title ?? null,
        reachFileCount: prop.reach_file_count ?? null,
        reachPrCount: prop.reach_pr_count ?? null,
        componentsReached: prop.components_reached ?? [],
        distinctDownstreamAuthors: prop.distinct_downstream_authors ?? null,
        maxPathDepth: prop.max_path_depth ?? null,
        persistenceDetected: prop.persistence_detected ?? null,
        effectiveDecayFactor: prop.effective_decay_factor ?? null,
        sourceAgeDays: prop.source_age_days ?? null,
        capApplied: prop.cap_applied ?? null,
        walkTruncated: prop.walk_truncated ?? null,
        reason: prop.reason ?? null,
      });
    }
  }

  const collaborators = [...collaboratorMap.entries()]
    .map(([id, v]) => ({
      actorClusterId: id,
      login: v.login,
      slug: bundle.engineersById.has(id) ? slugFor(id) : null,
      sharedEpisodes: v.sharedEpisodes,
      roles: [...v.roles],
    }))
    .sort((a, b) => b.sharedEpisodes - a.sharedEpisodes || a.login.localeCompare(b.login))
    .slice(0, 24);

  return {
    engineer,
    slug: slugFor(actorClusterId),
    thesisClaims: claimsFor(bundle, engineer.thesis_claim_ids).slice(0, THESIS_LIMIT),
    stabilityClaim: engineer.uncertainty?.claim_id ? (bundle.claimsById.get(engineer.uncertainty.claim_id) ?? null) : null,
    positions,
    featured,
    current,
    foundational,
    otherEpisodes,
    reviewInterventions: reviewInterventions.slice(0, 20),
    counterevidence: counterevidence.slice(0, 20),
    collaborators,
    propagation: propagation.slice(0, 6),
    episodePagesTruncated: otherIds.length > budget.other,
  };
}
