/**
 * The UI half of the render plan has to slice the same lists, in the same
 * order, with the same caps as `src/impact2/render_plan.py`. If it does not,
 * the package carries records the page never asks for and the page asks for
 * records the package does not carry, which is exactly the drift moving the
 * decision into Phase 2 was meant to end.
 *
 * These mirror `tests/phase2/test_render_plan.py` case for case. Change one
 * side and this side fails.
 */
import { describe, expect, it, vi } from 'vitest';

// data.ts is a server module; the parts under test here are pure.
vi.mock('server-only', () => ({}));

const { featuredEpisodeIds, renderedEpisodeIds } = await import('@/lib/data');

import type { Engineer } from '@/lib/schema';

const engineer = (over: Partial<Engineer> = {}): Engineer =>
  ({
    actor_cluster_id: 'actor/a',
    login: 'ada',
    display_name: 'Ada',
    strongest_evidence_episode_id: null,
    dimension_profile: [],
    current_episode_ids: [],
    foundational_episode_ids: [],
    episode_ids: [],
    thesis_claim_ids: [],
    rankable: true,
    roles_held: [],
    ...over,
  }) as unknown as Engineer;

const budget = { episodePages: 250, featured: 8, current: 6, foundational: 6, other: 40 };

describe('featuredEpisodeIds', () => {
  it('is the profile order, deduplicated', () => {
    const e = engineer({
      strongest_evidence_episode_id: 'e1',
      dimension_profile: [
        { dimension: 'product_outcome', top_episode_id: 'e2' },
        { dimension: 'reliability_risk', top_episode_id: 'e1' },
      ] as Engineer['dimension_profile'],
      current_episode_ids: ['e3'],
      foundational_episode_ids: ['e2'],
    });
    expect(featuredEpisodeIds(e)).toEqual(['e1', 'e2', 'e3']);
  });

  it('skips nulls rather than emitting them', () => {
    const e = engineer({
      strongest_evidence_episode_id: null,
      dimension_profile: [
        { dimension: 'product_outcome', top_episode_id: null },
        { dimension: 'reliability_risk', top_episode_id: 'e9' },
      ] as Engineer['dimension_profile'],
    });
    expect(featuredEpisodeIds(e)).toEqual(['e9']);
  });
});

describe('renderedEpisodeIds', () => {
  it('applies each cap to the list the page renders it from', () => {
    const e = engineer({
      strongest_evidence_episode_id: 'f0',
      current_episode_ids: ['c0', 'c1', 'c2'],
      foundational_episode_ids: ['n0', 'n1', 'n2'],
    });
    const ids = renderedEpisodeIds(e, { ...budget, featured: 3, current: 2, foundational: 2, other: 0 });
    // featured[:3] = f0, c0, c1; current[:2] = c0, c1; foundational[:2] = n0, n1
    expect(new Set(ids)).toEqual(new Set(['f0', 'c0', 'c1', 'n0', 'n1']));
  });

  it('treats "other" as attributed but not featured', () => {
    const e = engineer({
      strongest_evidence_episode_id: 'f0',
      episode_ids: ['f0', 'x1', 'x2', 'x3'],
    });
    const ids = renderedEpisodeIds(e, { ...budget, featured: 1, current: 0, foundational: 0, other: 2 });
    expect(new Set(ids)).toEqual(new Set(['f0', 'x1', 'x2']));
  });

  it('is deduplicated, because callers count over the result', () => {
    const e = engineer({
      strongest_evidence_episode_id: 'e1',
      current_episode_ids: ['e1', 'e2'],
      foundational_episode_ids: ['e2'],
    });
    const ids = renderedEpisodeIds(e, budget);
    expect(ids).toEqual(['e1', 'e2']);
  });

  it('renders nothing extra for an episode that is attributed but never listed', () => {
    const e = engineer({
      strongest_evidence_episode_id: 'f0',
      episode_ids: ['f0', 'extra'],
    });
    const ids = renderedEpisodeIds(e, { ...budget, featured: 1, current: 0, foundational: 0, other: 0 });
    expect(new Set(ids)).toEqual(new Set(['f0']));
  });
});
