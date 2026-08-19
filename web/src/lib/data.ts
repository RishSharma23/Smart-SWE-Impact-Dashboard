import 'server-only';

import fs from 'node:fs';
import path from 'node:path';

import {
  artifactSchema,
  claimsFileSchema,
  comparisonsSchema,
  coverageSchema,
  engineerSchema,
  episodeSchema,
  evidenceIndexSchema,
  indexesSchema,
  isSafeUrl,
  manifestSchema,
  methodologySchema,
  rankingsSchema,
  type Artifact,
  type Claim,
  type Comparisons,
  type Coverage,
  type Engineer,
  type Episode,
  type Indexes,
  type Manifest,
  type Methodology,
  type Rankings,
  type Scenario,
} from './schema';

const DATA_DIR = path.join(process.cwd(), '.data');

/**
 * How much of each surface the site renders.
 *
 * Phase 2 decides this now and publishes it as `render_plan` in the manifest,
 * because the package it writes carries exactly what these numbers allow it to
 * render. Deriving the same priority order here as well gave two
 * implementations in two languages, free to disagree about which episodes
 * exist. These values are the fallback for a package written before Phase 2
 * published its plan; they are the same numbers Phase 2 ships as defaults.
 */
const DEFAULT_RENDER_BUDGET = {
  episodePages: 250,
  featured: 8,
  current: 6,
  foundational: 6,
  other: 40,
} as const;

export type RenderBudget = { [K in keyof typeof DEFAULT_RENDER_BUDGET]: number };

export interface RenderPlan {
  /** Episodes with their own route, in the order Phase 2 chose. */
  episodePageIds: string[];
  /** Candidates past the cap. Truncation is reported, never hidden. */
  episodePagesTruncated: number;
  budget: RenderBudget;
  /** False when this package predates the published plan and it was derived here. */
  fromManifest: boolean;
}

function die(message: string): never {
  throw new Error(
    `\n\n  PHASE 2 DATA CONTRACT VIOLATION\n\n  ${message}\n\n` +
      `  The build is refusing the package rather than rendering something wrong.\n` +
      `  See contracts/PHASE_3_CONTRACT.md and report the defect back to Phase 2.\n`,
  );
}

function readJson(name: string): unknown {
  const file = path.join(DATA_DIR, name);
  if (!fs.existsSync(file)) {
    die(`${name} is missing from the staged package. Run \`npm run data\` first.`);
  }
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    die(`${name} is not valid JSON: ${(err as Error).message}`);
  }
}

function parse<T>(name: string, schema: { safeParse: (v: unknown) => { success: boolean; data?: T; error?: unknown } }, raw: unknown): T {
  const result = schema.safeParse(raw);
  if (!result.success) {
    const issues = (result.error as { issues?: { path: (string | number)[]; message: string }[] })?.issues ?? [];
    const shown = issues
      .slice(0, 12)
      .map((i) => `  - ${i.path.join('.') || '(root)'}: ${i.message}`)
      .join('\n');
    die(`${name} does not match export schema 1.0.0:\n${shown}${issues.length > 12 ? `\n  … and ${issues.length - 12} more` : ''}`);
  }
  return result.data as T;
}

// -- the bundle --------------------------------------------------------------

export interface Provenance {
  source_dir: string;
  is_fixture: boolean;
  demo_acknowledged: boolean;
  staged_at: string;
  total_bytes: number;
  file_count: number;
  /** Phase 2's own automated verdict, never rewritten by the UI. */
  export_publishable?: boolean;
  /** A human sign-off recorded at build time, when one exists. */
  publish_approval?: string | null;
}

export interface Bundle {
  provenance: Provenance;
  manifest: Manifest;
  rankings: Rankings;
  engineers: Engineer[];
  episodes: Episode[];
  comparisons: Comparisons;
  claims: Claim[];
  claimsById: Map<string, Claim>;
  correctionPathway: { enabled: boolean; instructions?: string | null; contact_field?: string | null } | null;
  methodology: Methodology;
  coverage: Coverage;
  indexes: Indexes;
  artifactsById: Map<string, Artifact>;
  engineersById: Map<string, Engineer>;
  episodesById: Map<string, Episode>;
  /** Episode ids that get their own route. */
  episodePageIds: Set<string>;
  /** Truncation is reported, never hidden. */
  episodePagesTruncated: number;
  /** Phase 2's decision about what this package can render. */
  renderPlan: RenderPlan;
  /** id -> URL slug, unique across engineers and episodes together. */
  slugs: Map<string, string>;
  /**
   * Non-fatal data defects found while loading. Surfaced on the coverage page
   * rather than swallowed — a defect the reader cannot see is worse than one
   * that fails the build.
   */
  dataWarnings: string[];
}

let cached: Bundle | null = null;

export function loadBundle(): Bundle {
  if (cached) return cached;

  const provenance = readJson('_provenance.json') as Provenance;
  const manifest = parse<Manifest>('dashboard_manifest.json', manifestSchema, readJson('dashboard_manifest.json'));
  const rankings = parse<Rankings>('rankings.json', rankingsSchema, readJson('rankings.json'));
  const engineers = parse<Engineer[]>('engineers.json', engineerSchema.array(), readJson('engineers.json'));
  const episodes = parse<Episode[]>('episodes.json', episodeSchema.array(), readJson('episodes.json'));
  const comparisons = parse<Comparisons>('comparisons.json', comparisonsSchema, readJson('comparisons.json'));
  const claimsFile = parse<{ claims: Claim[]; correction_pathway?: Bundle['correctionPathway'] }>(
    'claims.json',
    claimsFileSchema,
    readJson('claims.json'),
  );
  const methodology = parse<Methodology>('methodology.json', methodologySchema, readJson('methodology.json'));
  const coverage = parse<Coverage>('coverage.json', coverageSchema, readJson('coverage.json'));
  const indexes = parse<Indexes>('indexes.json', indexesSchema, readJson('indexes.json'));

  // -- evidence shards ------------------------------------------------------
  const evidenceIndex = parse<{ shards: Record<string, { file: string }> }>(
    'evidence.json',
    evidenceIndexSchema,
    readJson('evidence.json'),
  );
  const artifactsById = new Map<string, Artifact>();
  for (const [kind, shard] of Object.entries(evidenceIndex.shards ?? {})) {
    const rows = parse<Artifact[]>(shard.file, artifactSchema.array(), readJson(shard.file));
    for (const row of rows) {
      if (artifactsById.has(row.artifact_id)) {
        die(`duplicate artifact_id "${row.artifact_id}" in evidence shard ${kind}`);
      }
      artifactsById.set(row.artifact_id, row);
    }
  }

  const claimsById = new Map<string, Claim>();
  for (const claim of claimsFile.claims) {
    if (claimsById.has(claim.claim_id)) die(`duplicate claim_id "${claim.claim_id}" in claims.json`);
    claimsById.set(claim.claim_id, claim);
  }

  const engineersById = new Map<string, Engineer>();
  for (const e of engineers) {
    if (engineersById.has(e.actor_cluster_id)) die(`duplicate actor_cluster_id "${e.actor_cluster_id}" in engineers.json`);
    engineersById.set(e.actor_cluster_id, e);
  }

  const episodesById = new Map<string, Episode>();
  for (const ep of episodes) {
    if (episodesById.has(ep.episode_id)) die(`duplicate episode_id "${ep.episode_id}" in episodes.json`);
    episodesById.set(ep.episode_id, ep);
  }

  // -- what this package can render -----------------------------------------
  // Read Phase 2's plan before checking anything, because the plan is what says
  // which references this package is expected to resolve. A projected package
  // deliberately omits episodes nothing renders; a reference to one of those is
  // a link the UI never follows, not a defect.
  const plan = readRenderPlan(manifest, engineers, rankings, episodesById);

  // -- referential integrity -------------------------------------------------
  // Fatal: a missing claim means missing prose; a missing episode means a broken
  // route. Both would render something wrong, so the build stops.
  const orphans: string[] = [];
  // Recoverable: a participant whose actor was never clustered into a GitHub
  // account has no profile page, and RoleAttributionList already renders that
  // case as unlinked text. Reported, not fatal.
  const softOrphans: string[] = [];

  const checkClaim = (id: string | null | undefined, where: string) => {
    if (id && !claimsById.has(id)) orphans.push(`${where} -> unknown claim_id ${id}`);
  };
  const checkEpisode = (id: string | null | undefined, where: string) => {
    if (id && !episodesById.has(id)) orphans.push(`${where} -> unknown episode_id ${id}`);
  };
  const checkEngineer = (id: string | null | undefined, where: string) => {
    if (id && !engineersById.has(id)) orphans.push(`${where} -> unknown actor_cluster_id ${id}`);
  };

  // A profile renders the episodes its own lists name, and those must resolve.
  // The rest of `episode_ids` is a count and a set of ids, not a lookup.
  for (const e of engineers) {
    const w = `engineers[${e.actor_cluster_id}]`;
    e.thesis_claim_ids.forEach((c) => checkClaim(c, `${w}.thesis_claim_ids`));
    checkClaim(e.uncertainty?.claim_id, `${w}.uncertainty.claim_id`);
    renderedEpisodeIds(e, plan.budget).forEach((id) =>
      checkEpisode(id, `${w} renders episode`),
    );
  }

  const episodePageIds = new Set(plan.episodePageIds);
  for (const ep of episodes) {
    const w = `episodes[${ep.episode_id}]`;
    // Every episode renders its title claim, in cards and in tables.
    checkClaim(ep.title_claim_id, `${w}.title_claim_id`);
    ep.participants.forEach((p) => {
      if (!engineersById.has(p.actor_cluster_id)) {
        softOrphans.push(`${w}.participants -> ${p.actor_cluster_id} has no engineer profile`);
      }
    });
    // The narrative, the dimension rationales and the attribution sentences are
    // rendered by the episode page and nowhere else, so they are required where
    // there is a page and expected to be absent where there is not.
    if (!episodePageIds.has(ep.episode_id)) continue;
    checkClaim(ep.problem_claim_id, `${w}.problem_claim_id`);
    checkClaim(ep.intervention_claim_id, `${w}.intervention_claim_id`);
    checkClaim(ep.outcome_claim_id, `${w}.outcome_claim_id`);
    ep.dimensions.forEach((d) => checkClaim(d.rationale_claim_id, `${w}.dimensions.${d.dimension}.rationale_claim_id`));
    ep.participants.forEach((p) => {
      p.claim_ids.forEach((c) => checkClaim(c, `${w}.participants[${p.login}].claim_ids`));
    });
  }

  for (const scenario of rankings.scenarios) {
    for (const pos of scenario.positions) {
      checkEngineer(pos.actor_cluster_id, `rankings[${scenario.scenario}].positions`);
      pos.incomparable_with.forEach((id) => checkEngineer(id, `rankings[${scenario.scenario}].incomparable_with`));
    }
  }

  for (const [name, block] of Object.entries(comparisons.scenarios)) {
    block.top_five.forEach((id) => checkEngineer(id, `comparisons[${name}].top_five`));
    block.pairwise.forEach((p) => {
      checkEngineer(p.a, `comparisons[${name}].pairwise.a`);
      checkEngineer(p.b, `comparisons[${name}].pairwise.b`);
      checkClaim(p.explanation_claim_id, `comparisons[${name}].pairwise.explanation_claim_id`);
    });
  }

  coverage.limitations.claim_ids.forEach((c) => checkClaim(c, 'coverage.limitations.claim_ids'));

  const dataWarnings: string[] = [];
  if (softOrphans.length) {
    const kinds = new Map<string, number>();
    for (const o of softOrphans) {
      const prefix = o.split('-> ')[1]?.split('/').slice(0, 2).join('/') ?? 'unknown';
      kinds.set(prefix, (kinds.get(prefix) ?? 0) + 1);
    }
    dataWarnings.push(
      `${softOrphans.length} episode participant reference(s) point at an actor with no entry in ` +
        `engineers.json (${[...kinds].map(([k, n]) => `${k}: ${n}`).join(', ')}). These contributors are ` +
        `shown on episode pages without a profile link. Return to Phase 2: either cluster the identity or ` +
        `drop the participant row.`,
    );
    console.warn(`\n  data warning: ${dataWarnings[0]}\n`);
  }

  if (orphans.length) {
    die(
      `${orphans.length} orphan reference(s) in the package:\n` +
        orphans.slice(0, 15).map((o) => `  - ${o}`).join('\n') +
        (orphans.length > 15 ? `\n  … and ${orphans.length - 15} more` : ''),
    );
  }

  // -- required scenarios ----------------------------------------------------
  if (!rankings.scenarios.some((s) => s.scenario === rankings.default_scenario)) {
    die(`default_scenario "${rankings.default_scenario}" is not present in rankings.scenarios`);
  }
  if (!rankings.scenarios.some((s) => s.available && s.positions.length > 0)) {
    die('no ranking scenario is both available and populated — there is nothing to rank.');
  }

  // -- unsafe URLs -----------------------------------------------------------
  const unsafe: string[] = [];
  const checkUrl = (url: string | null | undefined, where: string) => {
    if (url && !isSafeUrl(url)) unsafe.push(`${where}: ${url}`);
  };
  for (const claim of claimsFile.claims) {
    claim.evidence.forEach((e) => checkUrl(e.url, `claims[${claim.claim_id}].evidence`));
  }
  for (const a of artifactsById.values()) checkUrl(a.url, `evidence[${a.artifact_id}]`);
  for (const ep of episodes) {
    ep.participants.forEach((p) =>
      p.direct_evidence.forEach((e) => checkUrl(e.url, `episodes[${ep.episode_id}].participants[${p.login}]`)),
    );
  }
  for (const e of engineers) {
    checkUrl(e.profile_url, `engineers[${e.actor_cluster_id}].profile_url`);
    checkUrl(e.avatar_url, `engineers[${e.actor_cluster_id}].avatar_url`);
  }
  if (unsafe.length) {
    die(
      `${unsafe.length} evidence URL(s) are not https://github.com — the UI will not render them:\n` +
        unsafe.slice(0, 10).map((u) => `  - ${u}`).join('\n'),
    );
  }

  // -- slugs -----------------------------------------------------------------
  // Engineers and episodes share one namespace so a slug is unambiguous.
  const usedSlugs = new Set<string>();
  const slugs = new Map<string, string>([
    ...buildSlugMap(engineers.map((e) => e.actor_cluster_id), usedSlugs),
    ...buildSlugMap([...episodePageIds], usedSlugs),
  ]);

  cached = {
    provenance,
    manifest,
    rankings,
    engineers,
    episodes,
    comparisons,
    claims: claimsFile.claims,
    claimsById,
    correctionPathway: claimsFile.correction_pathway ?? null,
    methodology,
    coverage,
    indexes,
    artifactsById,
    engineersById,
    episodesById,
    episodePageIds,
    episodePagesTruncated: plan.episodePagesTruncated,
    renderPlan: plan,
    slugs,
    dataWarnings,
  };
  return cached;
}

// -- the render plan ---------------------------------------------------------

function budgetFrom(raw: unknown): RenderBudget {
  const block = (raw ?? {}) as { episode_pages?: unknown; per_engineer?: Record<string, unknown> };
  const per = block.per_engineer ?? {};
  const num = (value: unknown, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : fallback;
  return {
    episodePages: num(block.episode_pages, DEFAULT_RENDER_BUDGET.episodePages),
    featured: num(per.featured, DEFAULT_RENDER_BUDGET.featured),
    current: num(per.current, DEFAULT_RENDER_BUDGET.current),
    foundational: num(per.foundational, DEFAULT_RENDER_BUDGET.foundational),
    other: num(per.other, DEFAULT_RENDER_BUDGET.other),
  };
}

/**
 * Take Phase 2's plan, or derive the same thing for a package that predates it.
 *
 * The derivation is kept because a package is a file on disk that outlives the
 * code that wrote it, not because the UI wants a second opinion: it runs only
 * when the manifest carries no plan, and it applies the identical rule.
 */
function readRenderPlan(
  manifest: Manifest,
  engineers: Engineer[],
  rankings: Rankings,
  episodesById: Map<string, Episode>,
): RenderPlan {
  const published = (manifest as { render_plan?: Record<string, unknown> }).render_plan;
  const budget = budgetFrom(published);

  if (published && Array.isArray(published.episode_page_ids)) {
    const ids = published.episode_page_ids.map(String);
    const missing = ids.filter((id) => !episodesById.has(id));
    if (missing.length) {
      die(
        `dashboard_manifest.json promises a page for ${missing.length} episode(s) that are not in ` +
          `episodes.json:\n${missing.slice(0, 8).map((m) => `  - ${m}`).join('\n')}\n\n` +
          `  The package and its own render plan disagree. Re-run \`make p2-export\`.`,
      );
    }
    const truncated = published.episode_pages_truncated;
    return {
      episodePageIds: ids,
      episodePagesTruncated: typeof truncated === 'number' ? truncated : 0,
      budget,
      fromManifest: true,
    };
  }

  const priority: string[] = [];
  const seen = new Set<string>();
  const engineersById = new Map(engineers.map((e) => [e.actor_cluster_id, e]));
  const addFor = (actor: string) => {
    const engineer = engineersById.get(actor);
    if (!engineer) return;
    for (const id of featuredEpisodeIds(engineer).slice(0, budget.featured)) {
      if (episodesById.has(id) && !seen.has(id)) {
        seen.add(id);
        priority.push(id);
      }
    }
  };
  // 1. the top five of every available scenario, the two-click evidence path.
  for (const scenario of rankings.scenarios) {
    if (!scenario.available) continue;
    for (const p of scenario.positions.slice().sort((a, b) => a.position - b.position).slice(0, 5)) {
      addFor(p.actor_cluster_id);
    }
  }
  // 2. everyone else who appears in a ranking at all.
  for (const scenario of rankings.scenarios) {
    for (const p of scenario.positions) addFor(p.actor_cluster_id);
  }
  return {
    episodePageIds: priority.slice(0, budget.episodePages),
    episodePagesTruncated: Math.max(0, priority.length - budget.episodePages),
    budget,
    fromManifest: false,
  };
}

/**
 * Every episode id one contributor profile puts on the page.
 *
 * The mirror of `_listing_ids` in `src/impact2/render_plan.py`, reading the same
 * caps out of the same manifest block. Both sides slice the same lists in the
 * same order, so the package holds exactly what the page displays and a build
 * against the full package renders identically to one against a projection.
 */
export function renderedEpisodeIds(engineer: Engineer, budget: RenderBudget): string[] {
  const featured = featuredEpisodeIds(engineer);
  const featuredSet = new Set(featured);
  const other = engineer.episode_ids.filter((id) => !featuredSet.has(id));
  // Deduplicated: the lists overlap (a current episode is usually featured too),
  // and callers count over the result. A repeated id would count a collaborator
  // twice.
  return [
    ...new Set([
      ...featured.slice(0, budget.featured),
      ...engineer.current_episode_ids.slice(0, budget.current),
      ...engineer.foundational_episode_ids.slice(0, budget.foundational),
      ...other.slice(0, budget.other),
    ]),
  ];
}

// -- selectors ---------------------------------------------------------------

/**
 * The episodes an engineer profile leads with. Every id here comes from a field
 * Phase 2 chose — there is no UI-side scoring, because there is no score.
 */
export function featuredEpisodeIds(engineer: Engineer): string[] {
  const out: string[] = [];
  const push = (id?: string | null) => {
    if (id && !out.includes(id)) out.push(id);
  };
  push(engineer.strongest_evidence_episode_id);
  for (const d of engineer.dimension_profile) push(d.top_episode_id);
  for (const id of engineer.current_episode_ids) push(id);
  for (const id of engineer.foundational_episode_ids) push(id);
  return out;
}

export function scenarioByName(bundle: Bundle, name: string): Scenario | undefined {
  return bundle.rankings.scenarios.find((s) => s.scenario === name);
}

export function defaultScenario(bundle: Bundle): Scenario {
  return (
    scenarioByName(bundle, bundle.rankings.default_scenario) ??
    bundle.rankings.scenarios.find((s) => s.available && s.positions.length > 0)!
  );
}

/**
 * URL slugs.
 *
 * Ids look like `github.com/PostHog/posthog#episode/40101-aaaa1111` and
 * `github/user/someone`. Percent-encoding those produces path segments
 * containing dots and slashes, which static hosts (GitHub Pages, Cloudflare,
 * `serve`) variously treat as files rather than routes. So the slug is the
 * distinctive trailing segment, sanitised, with uniqueness enforced when the
 * bundle loads rather than assumed.
 */
function baseSlug(id: string): string {
  const afterHash = id.includes('#') ? id.slice(id.indexOf('#') + 1) : id;
  const last = afterHash.split('/').filter(Boolean).pop() ?? afterHash;
  const cleaned = last
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return cleaned || 'item';
}

/** djb2 — deterministic across builds, unlike an iteration counter. */
function shortHash(input: string): string {
  let hash = 5381;
  for (let i = 0; i < input.length; i++) hash = ((hash << 5) + hash + input.charCodeAt(i)) >>> 0;
  return hash.toString(36);
}

function buildSlugMap(ids: Iterable<string>, used: Set<string>): Map<string, string> {
  const out = new Map<string, string>();
  for (const id of [...ids].sort()) {
    let slug = baseSlug(id);
    if (used.has(slug)) slug = `${slug}-${shortHash(id)}`;
    used.add(slug);
    out.set(id, slug);
  }
  return out;
}

/** Slug for any known engineer or episode id. */
export function slugFor(id: string): string {
  const { slugs } = loadBundle();
  return slugs.get(id) ?? baseSlug(id);
}

export function claimText(bundle: Bundle, id: string | null | undefined): Claim | null {
  if (!id) return null;
  return bundle.claimsById.get(id) ?? null;
}

export function resolveArtifacts(bundle: Bundle, ids: string[]): Artifact[] {
  return ids.map((id) => bundle.artifactsById.get(id)).filter((a): a is Artifact => Boolean(a));
}

/** Top five for a scenario, in position order, with the engineer joined in. */
export function topFive(bundle: Bundle, scenario: Scenario) {
  return scenario.positions
    .slice()
    .sort((a, b) => a.position - b.position)
    .slice(0, 5)
    .map((position) => ({ position, engineer: bundle.engineersById.get(position.actor_cluster_id)! }))
    .filter((row) => Boolean(row.engineer));
}

/** Engineers that appear in at least one scenario's positions. */
export function rankedEngineerIds(bundle: Bundle): Set<string> {
  const out = new Set<string>();
  for (const s of bundle.rankings.scenarios) for (const p of s.positions) out.add(p.actor_cluster_id);
  return out;
}
