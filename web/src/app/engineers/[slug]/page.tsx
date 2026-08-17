import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import * as React from 'react';

import { Avatar } from '@/components/Avatar';
import { ClaimList } from '@/components/Claim';
import { CounterevidencePanel } from '@/components/CounterevidencePanel';
import { DimensionBandChart, DimensionRadar } from '@/components/DimensionBandChart';
import { ImpactEpisodeCard } from '@/components/ImpactEpisodeCard';
import { ImpactTierBadge } from '@/components/ImpactTierBadge';
import { EngineerScenarioPosition } from '@/components/EngineerScenarioPosition';
import { SourceLink } from '@/components/SourceLink';
import { StabilityIndicator } from '@/components/StabilityIndicator';
import { Badge, Callout, Card, EmptyState, SectionHeading, TableScroll, DataTable, Td, Th } from '@/components/primitives';
import { loadBundle, slugFor } from '@/lib/data';
import { formatDate, formatNumber, humanize, roleLabel, statusLabel, titleize } from '@/lib/ui';
import { engineerView } from '@/lib/viewmodel';

export const dynamicParams = false;

export function generateStaticParams() {
  const bundle = loadBundle();
  return bundle.engineers.map((e) => ({ slug: slugFor(e.actor_cluster_id) }));
}

function resolve(slug: string) {
  const bundle = loadBundle();
  for (const e of bundle.engineers) {
    if (slugFor(e.actor_cluster_id) === slug) return { bundle, actorClusterId: e.actor_cluster_id };
  }
  return null;
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const found = resolve(slug);
  if (!found) return { title: 'Contributor not found' };
  const engineer = found.bundle.engineersById.get(found.actorClusterId)!;
  return {
    title: `@${engineer.login ?? engineer.actor_cluster_id}`,
    description: `Observable repository impact profile for @${engineer.login}: six evidence-banded dimensions, strongest episodes and ranking stability.`,
  };
}

export default async function EngineerPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const found = resolve(slug);
  if (!found) notFound();

  const { bundle } = found;
  const view = engineerView(bundle, found.actorClusterId);
  if (!view) notFound();

  const { engineer } = view;
  const current = view.current;
  const foundational = view.foundational;

  return (
    <div className="space-y-10">
      <nav aria-label="Breadcrumb" className="text-xs text-muted">
        <Link href="/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
          Overview
        </Link>
        <span className="mx-1.5">/</span>
        <Link href="/engineers/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
          Contributors
        </Link>
        <span className="mx-1.5">/</span>
        <span className="text-ink-soft">@{engineer.login}</span>
      </nav>

      {/* Identity + thesis. */}
      <header>
        <div className="flex flex-wrap items-start gap-5">
          <Avatar src={engineer.avatar_url} login={engineer.login ?? ''} size={72} />
          <div className="min-w-0 flex-1">
            <h1 className="text-3xl font-semibold leading-tight text-ink">
              {engineer.display_name ?? engineer.login}
            </h1>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-sm text-muted">
              <SourceLink href={engineer.profile_url}>@{engineer.login}</SourceLink>
              {engineer.active_period ? (
                <span>
                  active {formatDate(engineer.active_period.first_observed)} –{' '}
                  {formatDate(engineer.active_period.last_observed)}
                </span>
              ) : null}
            </p>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              <Badge tone="unknown">Affiliation unknown</Badge>
              {engineer.rankable ? null : <Badge tone="warn">Not ranked</Badge>}
              {engineer.identity_ambiguity === 'ambiguous' ? <Badge tone="warn">Identity ambiguous</Badge> : null}
              {engineer.concentration_profile ? (
                <Badge tone="outline">{titleize(engineer.concentration_profile)}</Badge>
              ) : null}
            </div>
          </div>
        </div>

        {engineer.affiliation_note ? (
          <p className="mt-3 max-w-3xl text-xs leading-relaxed text-muted">{engineer.affiliation_note}</p>
        ) : null}

        {engineer.identity_ambiguity === 'ambiguous' && engineer.identity_ambiguity_reasons?.length ? (
          <Callout tone="warn" title="This identity may merge more than one account" className="mt-4 max-w-3xl">
            <ul className="list-disc space-y-0.5 pl-4">
              {engineer.identity_ambiguity_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </Callout>
        ) : null}
      </header>

      {/* Impact thesis. */}
      <section aria-labelledby="thesis-heading">
        <SectionHeading id="thesis-heading" title="Impact thesis" eyebrow="what the evidence supports" />
        {view.thesisClaims.length > 0 ? (
          <Card>
            <ClaimList claims={view.thesisClaims} tone="lead" />
          </Card>
        ) : (
          <EmptyState title="No portfolio thesis claim survived evidence checks.">
            Phase 2 rejects claims it cannot trace to an artifact. Nothing is written in their place.
          </EmptyState>
        )}
      </section>

      {/* Eligibility, when excluded. */}
      {!engineer.rankable ? (
        <Callout tone="warn" title="Excluded from every ranking scenario">
          <p className="mb-2">
            {engineer.eligibility_label
              ? humanize(engineer.eligibility_label)
              : 'This contributor did not clear the evidence bar.'}{' '}
            This says what a 90-day public window could observe. It is not a statement about the person or their work
            elsewhere.
          </p>
          {engineer.eligibility_reasons.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-4">
              {engineer.eligibility_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : null}
        </Callout>
      ) : null}

      {/* Six-dimension profile. */}
      <section aria-labelledby="dimensions-heading">
        <SectionHeading
          id="dimensions-heading"
          title="Six-dimension evidence profile"
          description="Ordinal bands, not scores. A hatched slot means the public record could not assess that dimension — it is not a zero, and it is excluded from pairwise comparison rather than counted against anyone."
        />
        <div className="grid gap-5 lg:grid-cols-[1fr_auto]">
          <Card>
            <DimensionBandChart profile={engineer.dimension_profile} highlight={engineer.strongest_dimension} />
          </Card>
          <Card className="grid place-items-center lg:w-[19rem]">
            <DimensionRadar profile={engineer.dimension_profile} />
          </Card>
        </div>
      </section>

      {/* Ranking position across scenarios + stability. */}
      <section aria-labelledby="stability-heading">
        <SectionHeading
          id="stability-heading"
          title="Position and how firm it is"
          description="A position is only meaningful with the weighting that produced it and the range it moves through under resampling."
        />
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <EngineerScenarioPosition positions={view.positions} login={engineer.login ?? ''} />
          </Card>
          <Card>
            <h3 className="mb-3 text-sm font-semibold text-ink">Rank stability</h3>
            <StabilityIndicator stability={engineer.uncertainty ?? null} />
            {view.stabilityClaim ? <ClaimList claims={[view.stabilityClaim]} className="mt-3" /> : null}
          </Card>
        </div>
      </section>

      {/* Current vs foundational. */}
      <section aria-labelledby="current-foundational-heading">
        <SectionHeading
          id="current-foundational-heading"
          title="Current versus foundational work"
          description="Current work landed recently in the window. Foundational work is persistent or high-leverage — other people are still building on it."
        />
        <div className="grid gap-5 lg:grid-cols-2">
          <div>
            <h3 className="mb-2.5 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
              Current ({current.length})
            </h3>
            {current.length === 0 ? (
              <EmptyState title="No episode was classified as current.">
                Not observed in public data for this window.
              </EmptyState>
            ) : (
              <ul className="space-y-3">
                {current.slice(0, 5).map((ep) => (
                  <li key={ep.episodeId}>
                    <ImpactEpisodeCard episode={ep} compact />
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h3 className="mb-2.5 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
              Foundational ({foundational.length})
            </h3>
            {foundational.length === 0 ? (
              <EmptyState title="No episode was classified as foundational.">
                Foundational work that predates the 90-day window is invisible to this analysis.
              </EmptyState>
            ) : (
              <ul className="space-y-3">
                {foundational.slice(0, 5).map((ep) => (
                  <li key={ep.episodeId}>
                    <ImpactEpisodeCard episode={ep} compact />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      {/* Strongest episodes with role-specific attribution. */}
      <section aria-labelledby="episodes-heading">
        <SectionHeading
          id="episodes-heading"
          title="Strongest evidence episodes"
          description="Each of these is the episode Phase 2 selected as the strongest carrier of a specific dimension, or of the profile overall. The role shown is this contributor's role in that episode."
        />
        {view.featured.length === 0 ? (
          <EmptyState title="No featured episode was published for this contributor." />
        ) : (
          <ul className="grid gap-4 lg:grid-cols-2">
            {view.featured.map((ep) => (
              <li key={ep.episodeId}>
                <ImpactEpisodeCard episode={ep} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Propagation / durability. */}
      <section aria-labelledby="propagation-heading">
        <SectionHeading
          id="propagation-heading"
          title="Propagation and durability"
          description="How far the strongest work reached through the import graph, and how much of that reach is still being built on."
        />
        {view.propagation.length === 0 ? (
          <EmptyState title="Propagation was not measured for this contributor's featured episodes.">
            Rust, Go, SQL, Hog and Ruby have no import parser, so reach for work confined to them is unknown rather than
            small.
          </EmptyState>
        ) : (
          <TableScroll label="Propagation per featured episode">
            <DataTable>
              <caption className="sr-only">
                Propagation reach and durability measurements for each featured episode.
              </caption>
              <thead>
                <tr>
                  <Th>Episode</Th>
                  <Th>Files reached</Th>
                  <Th>Downstream PRs</Th>
                  <Th>Other authors</Th>
                  <Th>Components</Th>
                  <Th>Depth</Th>
                  <Th>Still built on</Th>
                  <Th>Decay</Th>
                </tr>
              </thead>
              <tbody>
                {view.propagation.map((p) => (
                  <tr key={p.episodeId}>
                    <Th scope="row" className="bg-surface normal-case tracking-normal">
                      {p.slug ? (
                        <Link href={`/episodes/${p.slug}/`} className="font-medium text-ink hover:underline">
                          {p.title ?? p.episodeId}
                        </Link>
                      ) : (
                        <span className="text-ink">{p.title ?? p.episodeId}</span>
                      )}
                      {p.capApplied || p.walkTruncated ? (
                        <Badge tone="warn" className="ml-2">
                          reach is a lower bound
                        </Badge>
                      ) : null}
                    </Th>
                    <Td className="font-mono">{formatNumber(p.reachFileCount)}</Td>
                    <Td className="font-mono">{formatNumber(p.reachPrCount)}</Td>
                    <Td className="font-mono">{formatNumber(p.distinctDownstreamAuthors)}</Td>
                    <Td className="font-mono">{p.componentsReached.length}</Td>
                    <Td className="font-mono">{formatNumber(p.maxPathDepth)}</Td>
                    <Td>{p.persistenceDetected ? 'yes' : 'no'}</Td>
                    <Td className="font-mono">{p.effectiveDecayFactor?.toFixed(3) ?? '—'}</Td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </TableScroll>
        )}
      </section>

      {/* Review interventions. */}
      <section aria-labelledby="review-heading">
        <SectionHeading
          id="review-heading"
          title="Consequential review interventions"
          description="Review comments where the thread resolved and the code changed afterwards. Causality here is inferred from ordering, thread resolution and GitHub's outdated flag — it is evidence, not proof."
        />
        {view.reviewInterventions.length === 0 ? (
          <EmptyState title="No consequential review intervention was observed.">
            {bundle.coverage.capabilities_disabled?.review_intervention_candidates ??
              'Not observed in public data for this window.'}
          </EmptyState>
        ) : (
          <ul className="space-y-2.5">
            {view.reviewInterventions.map((r, i) => (
              <li key={`${r.episodeId}-${i}`} className="card p-3.5">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  {r.role ? <Badge tone="accent">{roleLabel(r.role)}</Badge> : null}
                  {r.slug ? (
                    <Link href={`/episodes/${r.slug}/`} className="text-xs font-medium text-ink hover:underline">
                      {r.title}
                    </Link>
                  ) : (
                    <span className="text-xs font-medium text-ink">{r.title}</span>
                  )}
                </div>
                {r.detail ? <p className="text-sm leading-relaxed text-ink-soft">{r.detail}</p> : null}
                {r.url ? (
                  <p className="mt-1">
                    <SourceLink href={r.url} className="text-xs">
                      {r.url.replace('https://github.com/PostHog/posthog/', '')}
                    </SourceLink>
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Counterevidence and uncertainty. */}
      <section aria-labelledby="counter-heading">
        <SectionHeading
          id="counter-heading"
          title="Counterevidence and uncertainty"
          description="Everything in the record that argues against the profile above."
        />
        <CounterevidencePanel
          items={view.counterevidence.map((c) => ({
            kind: c.kind,
            detail: c.detail,
            evidence_tier: c.evidenceTier,
            requires_human_confirmation: c.requiresHumanConfirmation,
            pr_number: c.prNumber,
            episodeId: c.episodeId,
            episodeTitle: c.episodeTitle,
            slug: c.slug,
          }))}
          emptyNote="No revert, regression candidate or corrective-burden signal was attached to this contributor's episodes. Absence of a signal is not proof of correctness."
        />
      </section>

      {/* Collaborators. */}
      <section aria-labelledby="collab-heading">
        <SectionHeading
          id="collab-heading"
          title="Shared credit"
          description="Every episode here has other people in it. Impact is not sole authorship, and this dashboard never implies it is."
        />
        {view.collaborators.length === 0 ? (
          <EmptyState title="No co-participant was attributed to these episodes." />
        ) : (
          <ul className="flex flex-wrap gap-2">
            {view.collaborators.map((c) => (
              <li key={c.actorClusterId}>
                <span className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs">
                  {c.slug ? (
                    <Link href={`/engineers/${c.slug}/`} className="font-mono font-medium text-ink hover:underline">
                      @{c.login}
                    </Link>
                  ) : (
                    <span className="font-mono font-medium text-ink">@{c.login}</span>
                  )}
                  <span className="text-muted">
                    {c.sharedEpisodes} shared {c.sharedEpisodes === 1 ? 'episode' : 'episodes'}
                  </span>
                  {c.roles.length > 0 ? (
                    <span className="text-muted">· {c.roles.map(roleLabel).join(', ')}</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Everything else. */}
      {view.otherEpisodes.length > 0 ? (
        <section aria-labelledby="all-episodes-heading">
          <SectionHeading
            id="all-episodes-heading"
            title={`Other attributed episodes (${formatNumber(view.otherEpisodes.length)})`}
            description="In date order. Listing an episode is not a claim about its impact — the dimension bands above are where that judgement lives."
          />
          <TableScroll label="All other attributed episodes">
            <DataTable>
              <thead>
                <tr>
                  <Th>Episode</Th>
                  <Th>Status</Th>
                  <Th>Release</Th>
                  <Th>Credit</Th>
                  <Th>Started</Th>
                </tr>
              </thead>
              <tbody>
                {view.otherEpisodes.map((ep) => (
                  <tr key={ep.episodeId}>
                    <Th scope="row" className="bg-surface normal-case tracking-normal">
                      {ep.slug ? (
                        <Link href={`/episodes/${ep.slug}/`} className="font-medium text-ink hover:underline">
                          {ep.title ?? ep.episodeId}
                        </Link>
                      ) : (
                        <span className="text-ink">{ep.title ?? ep.episodeId}</span>
                      )}
                    </Th>
                    <Td className="text-xs">{statusLabel(ep.status)}</Td>
                    <Td className="text-xs">
                      {ep.releaseCorroboration === 'corroborated' ? 'corroborated' : 'merged only'}
                    </Td>
                    <Td className="text-xs">{ep.shareCategory ?? 'unclear'}</Td>
                    <Td className="whitespace-nowrap font-mono text-xs">{formatDate(ep.startedAt)}</Td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </TableScroll>
          {view.episodePagesTruncated ? (
            <p className="mt-2 text-xs text-muted">
              This list is capped. The full set of attributed episode ids is in{' '}
              <code className="font-mono">engineers.json</code> in the published data package.
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
