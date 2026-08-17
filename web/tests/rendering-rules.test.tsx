/**
 * The rules that are easy to get wrong and expensive to get wrong.
 * Each test names the contract clause it defends.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { Claim, ClaimList } from '@/components/Claim';
import { CounterevidencePanel } from '@/components/CounterevidencePanel';
import { DimensionBandChart, DimensionRadar } from '@/components/DimensionBandChart';
import { ImpactEpisodeCard, ReleaseQualifier } from '@/components/ImpactEpisodeCard';
import { ImpactTierBadge } from '@/components/ImpactTierBadge';
import { PairwiseExplanation } from '@/components/PairwiseExplanation';
import { RoleAttributionList } from '@/components/RoleAttributionList';
import { SourceLink } from '@/components/SourceLink';
import { StabilityIndicator } from '@/components/StabilityIndicator';
import { isSafeUrl } from '@/lib/schema';
import { bandName } from '@/lib/ui';

import { claim, dimension, episodeSummary, pairwise, participant, unknownDimension } from './fixtures';

describe('§3 the claim contract', () => {
  it('renders the claim text as the visible prose', () => {
    const { container } = render(<Claim claim={claim({ text: 'A specific traceable sentence.' })} />);
    // The text also appears in the trigger's screen-reader label, which is why
    // this asserts on the visible paragraph specifically.
    expect(container.querySelector('p')?.textContent).toContain('A specific traceable sentence.');
  });

  it('renders NOTHING for a null claim — not a placeholder, not the raw field', () => {
    const { container } = render(<Claim claim={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('skips null claims inside a list without leaving a gap', () => {
    const { container } = render(<ClaimList claims={[null, undefined, claim()]} />);
    expect(container.querySelectorAll('p').length).toBe(1);
  });

  it('exposes evidence and the copyable claim_id behind one click', async () => {
    const user = userEvent.setup();
    render(<Claim claim={claim({ claim_id: 'claim/deadbeef' })} />);
    await user.click(screen.getByRole('button', { name: /evidence/i }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('claim/deadbeef')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /PostHog\/posthog\/pull\/1/ })).toHaveAttribute(
      'rel',
      expect.stringContaining('noopener'),
    );
  });

  it('says so plainly when a claim carries no artifact', async () => {
    const user = userEvent.setup();
    render(<Claim claim={claim({ evidence: [], evidence_count: 0 })} />);
    await user.click(screen.getByRole('button', { name: /evidence/i }));
    expect(screen.getByText(/cannot be traced further/i)).toBeInTheDocument();
  });
});

describe('§5.2 null is not zero', () => {
  it('labels an unassessable dimension "not assessable", never 0', () => {
    render(<DimensionBandChart profile={[unknownDimension()]} />);
    expect(screen.getAllByText(/not assessable/i).length).toBeGreaterThan(0);
    expect(screen.queryByText('band 0.00')).not.toBeInTheDocument();
  });

  it('gives the unknown reason, not a bare blank', () => {
    render(<DimensionBandChart profile={[unknownDimension()]} />);
    expect(screen.getAllByText(/review_intervention_candidates is absent/).length).toBeGreaterThan(0);
  });

  it('describes unknown spokes as not assessable in the radar alt text', () => {
    render(
      <DimensionRadar
        profile={[dimension(), dimension({ dimension: 'reliability_risk' }), unknownDimension()]}
      />,
    );
    expect(screen.getByRole('img').getAttribute('aria-label')).toMatch(/not assessable/i);
  });

  it('bandName maps null to "not assessable" rather than the band-0 name', () => {
    expect(bandName(null)).toBe('not assessable');
    expect(bandName(0)).toBe('none observed');
  });

  it('every chart has a tabular equivalent', () => {
    render(<DimensionBandChart profile={[dimension(), unknownDimension()]} />);
    // Two tables: the dimension table and the aggregation trace beneath it.
    expect(screen.getAllByRole('table').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Six impact dimensions with band value/i)).toBeInTheDocument();
  });
});

describe('§6.1 merged is not released', () => {
  it('qualifies a merged_only episode', () => {
    render(<ReleaseQualifier value="merged_only" />);
    expect(screen.getByText(/release not corroborated/i)).toBeInTheDocument();
  });

  it('treats a missing release_corroboration as uncorroborated, not as shipped', () => {
    render(<ReleaseQualifier value={null} />);
    expect(screen.getByText(/release not corroborated/i)).toBeInTheDocument();
  });

  it('marks a corroborated release distinctly', () => {
    render(<ReleaseQualifier value="corroborated" />);
    expect(screen.getByText(/release corroborated/i)).toBeInTheDocument();
  });
});

describe('§6.2 counterevidence', () => {
  it('never calls an unconfirmed candidate a regression', () => {
    render(
      <CounterevidencePanel
        items={[
          {
            kind: 'regression_candidate',
            detail: 'a later PR touches the same file',
            evidence_tier: 'C',
            requires_human_confirmation: true,
            pr_number: 42,
          },
        ]}
      />,
    );
    expect(screen.getByText(/unconfirmed — needs human review/i)).toBeInTheDocument();
    // Split across a <strong>, so assert on the rendered text as a whole.
    expect(document.body.textContent).toMatch(/is\s+not\s+a regression/i);
  });

  it('says absence of a signal is not a clean bill of health', () => {
    render(<CounterevidencePanel items={[]} />);
    expect(screen.getByText(/not a clean bill of health/i)).toBeInTheDocument();
  });
});

describe('§6.3 shared credit is a category, never a percentage', () => {
  it('renders the share category and no percentage', () => {
    const { container } = render(<RoleAttributionList participants={[participant()]} />);
    expect(screen.getByText('Primary credit')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\d+\s?%/);
  });

  it('renders "unclear" as unclear, not as 0%', () => {
    render(<RoleAttributionList participants={[participant({ share_category: 'unclear' })]} />);
    expect(screen.getByText('Credit unclear')).toBeInTheDocument();
  });

  it('always shows every contributor', () => {
    render(
      <RoleAttributionList
        participants={[participant(), participant({ actor_cluster_id: 'github/user/grace', login: 'grace' })]}
      />,
    );
    expect(screen.getByText('@ada')).toBeInTheDocument();
    expect(screen.getByText('@grace')).toBeInTheDocument();
  });
});

describe('tiers, incomparability and stability', () => {
  it('shows tier as the primary grouping and position as a detail', () => {
    render(<ImpactTierBadge tier={2} position={3} sharedWith={1} />);
    expect(screen.getByText('Tier 2')).toBeInTheDocument();
    expect(screen.getByText(/position 3/)).toBeInTheDocument();
    expect(screen.getByText(/shared tier/)).toBeInTheDocument();
  });

  it('reports a cross-check disagreement rather than averaging it away', () => {
    render(<StabilityIndicator stability={{ rank_stability_index: 0.9 }} crossCheckDelta={2} />);
    expect(screen.getByText(/two aggregation methods disagree/i)).toBeInTheDocument();
  });

  it('says stability was unmeasured instead of implying certainty', () => {
    render(<StabilityIndicator stability={null} />);
    expect(screen.getByText(/was not measured/i)).toBeInTheDocument();
  });

  it('shows a position range, not a single point', () => {
    render(<StabilityIndicator stability={{ rank_stability_index: 0.86, position_range: [1, 4] }} />);
    expect(screen.getByText(/positions 1–4/)).toBeInTheDocument();
  });
});

describe('§7 excluded criteria are visible', () => {
  it('renders excluded criteria with both sides’ unknown reasons', () => {
    render(<PairwiseExplanation pair={pairwise()} />);
    expect(screen.getByText(/no import parser for Rust/)).toBeInTheDocument();
    expect(screen.getByText(/rather than scored as zero/i)).toBeInTheDocument();
  });

  it('shows n/a rather than 0 for an unknown criterion value', () => {
    render(<PairwiseExplanation pair={pairwise()} />);
    const table = screen.getByRole('table');
    expect(within(table).getAllByText('n/a').length).toBeGreaterThan(0);
  });
});

describe('URL safety', () => {
  it('accepts github.com over https only', () => {
    expect(isSafeUrl('https://github.com/PostHog/posthog/pull/1')).toBe(true);
    expect(isSafeUrl('http://github.com/x')).toBe(false);
    expect(isSafeUrl('https://evil.example.com/x')).toBe(false);
    expect(isSafeUrl('javascript:alert(1)')).toBe(false);
    expect(isSafeUrl(null)).toBe(false);
  });

  it('refuses to render an unsafe URL as a link', () => {
    render(<SourceLink href="https://evil.example.com/x">click</SourceLink>);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('adds noopener noreferrer to a safe external link', () => {
    render(<SourceLink href="https://github.com/PostHog/posthog">repo</SourceLink>);
    expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer external');
  });
});

describe('edge cases every component must survive', () => {
  it('renders a very long episode title without crashing', () => {
    const long = 'x'.repeat(600);
    render(<ImpactEpisodeCard episode={episodeSummary({ title: long })} />);
    expect(screen.getByText(long)).toBeInTheDocument();
  });

  it('renders a long narrative claim', () => {
    const long = 'A very long narrative sentence. '.repeat(60);
    const { container } = render(<Claim claim={claim({ text: long })} />);
    expect(container.querySelector('p')?.textContent).toContain('A very long narrative sentence.');
  });

  it('handles a high-count dimension profile with mixed unknowns', () => {
    render(
      <DimensionBandChart
        profile={[
          dimension(),
          dimension({ dimension: 'reliability_risk', value: 0 }),
          unknownDimension('engineering_leverage'),
          unknownDimension('decision_quality'),
          dimension({ dimension: 'propagation_durability', value: 4 }),
          unknownDimension(),
        ]}
      />,
    );
    expect(screen.getAllByText(/not assessable/i).length).toBeGreaterThanOrEqual(3);
    // Once in the band-scale legend, once as the band 0 row's label.
    expect(screen.getAllByText('none observed').length).toBeGreaterThanOrEqual(2);
  });

  it('renders an episode with no PRs, no components and no roles', () => {
    render(
      <ImpactEpisodeCard
        episode={episodeSummary({ prNumbers: [], components: [], roles: [], shareCategory: null, slug: null })}
      />,
    );
    expect(screen.getByText('Session replay export pipeline')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
