import Link from 'next/link';
import * as React from 'react';

import { EvidenceChipLegend } from '@/components/EvidenceChip';
import { DemoDataBanner, LimitationBanner, ProvisionalBanner } from '@/components/LimitationBanner';
import { OverviewLeaders, ScenarioComparison } from '@/components/OverviewLeaders';
import { Badge, Card, EmptyState, KeyValue, SectionHeading } from '@/components/primitives';
import { defaultScenario, loadBundle, slugFor } from '@/lib/data';
import { formatDate, formatNumber, shortSha } from '@/lib/ui';
import { leadersByScenario, pairwiseByScenario, scenarioMovement } from '@/lib/viewmodel';

export default function OverviewPage() {
  const bundle = loadBundle();
  const { manifest, coverage, provenance } = bundle;
  const scenario = defaultScenario(bundle);
  const leaders = leadersByScenario(bundle);
  const pairs = pairwiseByScenario(bundle);
  const movement = scenarioMovement(bundle);

  const unrankable = bundle.engineers.filter((e) => !e.rankable);

  return (
    <div className="space-y-10">
      {provenance.is_fixture ? <DemoDataBanner note={(manifest as { fixture_note?: string }).fixture_note} /> : null}
      {!manifest.publishable ? (
        <ProvisionalBanner
          blockers={manifest.publishable_blockers}
          validationStatus={manifest.validation_status}
          approval={provenance.publish_approval ?? null}
        />
      ) : null}

      {/* Executive summary in plain language. */}
      <section aria-labelledby="summary-heading" className="max-w-3xl">
        <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          {formatDate(manifest.window.start)} – {formatDate(manifest.window.end)} ·{' '}
          {manifest.source.repository_url.replace('https://github.com/', '')} @ {shortSha(manifest.source.analyzed_head_sha, 8)}
        </p>
        <h1 id="summary-heading" className="text-3xl font-semibold leading-tight text-ink sm:text-4xl">
          Who moved the product, the platform and the people around them — and what proves it.
        </h1>
        <div className="prose-editorial mt-4 text-[15px] text-ink-soft">
          <p>
            This analysis reads {formatNumber(manifest.counts.episodes)} impact episodes — connected arcs of work, not
            individual pull requests — across {formatNumber(manifest.counts.engineers)} contributors, and ranks the{' '}
            {formatNumber(manifest.counts.rankable_engineers)} with enough observable evidence to compare. It does that
            by outranking them pairwise on six dimensions, publishing the trace for every comparison, and refusing to
            reduce any of it to a single number.
          </p>
          <p>
            Every sentence below is a claim with an artifact behind it. Two clicks from any statement gets you to the
            pull request, review comment or feature flag it rests on. Where the public record could not settle
            something, it says so rather than guessing.
          </p>
        </div>
      </section>

      <LimitationBanner headline={coverage.limitations.headline ?? manifest.limitations_headline} />

      {/* Coverage strip — analysis statistics, explicitly not productivity. */}
      <section aria-labelledby="coverage-strip-heading">
        <h2 id="coverage-strip-heading" className="sr-only">
          What was analysed
        </h2>
        <Card className="p-0">
          <dl className="grid grid-cols-2 divide-line sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
            <StripItem label="Impact episodes" value={formatNumber(manifest.counts.episodes)} hint="connected arcs of work" />
            <StripItem
              label="Contributors seen"
              value={formatNumber(manifest.counts.engineers)}
              hint={`${formatNumber(manifest.counts.rankable_engineers)} rankable`}
            />
            <StripItem
              label="Evidence claims"
              value={formatNumber(manifest.counts.claims)}
              hint="each traceable to an artifact"
            />
            <StripItem
              label="Propagation edges"
              value={formatNumber(manifest.counts.propagation_edges)}
              hint="downstream reach measured"
            />
            <StripItem
              label="Analysed commit"
              value={shortSha(manifest.source.analyzed_head_sha, 8)}
              hint={manifest.source.is_shallow_clone ? 'shallow clone' : 'full clone'}
            />
            <StripItem
              label="Audit status"
              value={manifest.validation_status.replace(/_/g, ' ')}
              hint={`publishable: ${manifest.publishable}`}
            />
          </dl>
        </Card>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          These are coverage statistics describing what the analysis could see. They are <strong>not</strong>{' '}
          productivity statistics, and none of them feeds a ranking.{' '}
          <Link href="/coverage/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
            Full coverage and missingness report
          </Link>
          .
        </p>
      </section>

      <OverviewLeaders leadersByScenario={leaders} pairwiseByScenario={pairs} />

      <ScenarioComparison movement={movement} />

      {/* Engineers the model refused to rank. */}
      <section aria-labelledby="unrankable-heading">
        <SectionHeading
          id="unrankable-heading"
          title="Contributors with insufficient observable evidence"
          description="These contributors appear in the data but are excluded from every ranking. That is a statement about the evidence available in a 90-day public window, not about them."
        />
        {unrankable.length === 0 ? (
          <EmptyState title="Every contributor in this package cleared the evidence bar.">
            No exclusions were recorded for this run.
          </EmptyState>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {unrankable.slice(0, 24).map((e) => (
              <li key={e.actor_cluster_id}>
                <Card className="h-full p-4">
                  <div className="flex items-center justify-between gap-2">
                    <Link
                      href={`/engineers/${slugFor(e.actor_cluster_id)}/`}
                      className="truncate font-mono text-sm font-medium text-ink hover:underline"
                    >
                      @{e.login ?? e.actor_cluster_id}
                    </Link>
                    <Badge tone="unknown">not ranked</Badge>
                  </div>
                  {e.eligibility_reasons.length > 0 ? (
                    <ul className="mt-2 space-y-1">
                      {e.eligibility_reasons.slice(0, 3).map((r) => (
                        <li key={r} className="text-xs leading-relaxed text-muted">
                          {r}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-xs italic text-unknown">No reason was recorded.</p>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        )}
        {unrankable.length > 24 ? (
          <p className="mt-3 text-xs text-muted">
            Showing 24 of {formatNumber(unrankable.length)}.{' '}
            <Link href="/engineers/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
              See all contributors
            </Link>
            .
          </p>
        ) : null}
      </section>

      {/* How to read the evidence chips. */}
      <section aria-labelledby="chips-heading">
        <SectionHeading
          id="chips-heading"
          title="How to read the evidence labels"
          description="Every claim carries one of these. Shape and text carry the same information as colour."
        />
        <Card>
          <EvidenceChipLegend />
        </Card>
      </section>

      <section aria-labelledby="method-note-heading" className="max-w-3xl">
        <SectionHeading
          id="method-note-heading"
          title="Why there is no score"
          description={bundle.rankings.method?.why_not_a_score ?? undefined}
        />
        <p className="text-sm leading-relaxed text-ink-soft">
          {bundle.rankings.method?.tiers_explained}{' '}
          <Link href="/methodology/" className="font-medium text-d2 underline underline-offset-2">
            Read the full methodology
          </Link>
          .
        </p>
      </section>
    </div>
  );
}

function StripItem({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="border-b border-line p-4 last:border-b-0 lg:border-b-0">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 font-mono text-lg font-semibold leading-tight text-ink">{value}</dd>
      {hint ? <p className="mt-0.5 text-[11px] leading-snug text-muted">{hint}</p> : null}
    </div>
  );
}
