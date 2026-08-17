'use client';

import { ArrowRight, Unlink } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import type { LeaderView, PairwiseView } from '@/lib/viewmodel';
import { bandName, cn, dimensionColor, dimensionLabel, roleLabel, sortDimensions } from '@/lib/ui';

import { Avatar } from './Avatar';
import { Claim } from './Claim';
import { DimensionBandChart } from './DimensionBandChart';
import { ImpactEpisodeCard } from './ImpactEpisodeCard';
import { ImpactTierBadge } from './ImpactTierBadge';
import { StabilityIndicator } from './StabilityIndicator';
import { WhyThisRanking } from './PairwiseExplanation';
import { Badge, EmptyState } from './primitives';

export function LeaderCard({
  leader,
  pairs,
  scenarioLabel,
  className,
}: {
  leader: LeaderView;
  pairs: PairwiseView[];
  scenarioLabel: string;
  className?: string;
}) {
  const strongest = sortDimensions(leader.dimensionProfile)
    .filter((d) => !d.is_unknown && d.value !== null)
    .sort((a, b) => (b.value as number) - (a.value as number))
    .slice(0, 2);

  return (
    <article data-leader-card="" className={cn('card card-interactive overflow-hidden p-0', className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-surface-sunken/50 px-5 py-3.5">
        <ImpactTierBadge tier={leader.tier} position={leader.position} sharedWith={leader.sharedTierWith.length} />
        <StabilityIndicator stability={leader.stability} compact className="max-w-full" />
      </div>

      <div className="p-5">
        <div className="flex items-start gap-3.5">
          <Avatar src={leader.avatarUrl} login={leader.login} size={48} />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-lg font-semibold leading-tight text-ink">
              <Link
                href={`/engineers/${leader.slug}/`}
                className="hover:underline hover:decoration-accent hover:underline-offset-4"
              >
                {leader.displayName}
              </Link>
            </h3>
            <p className="truncate font-mono text-xs text-muted">@{leader.login}</p>
            {leader.identityAmbiguity === 'ambiguous' ? (
              <Badge tone="warn" className="mt-1.5">
                Identity may merge two accounts
              </Badge>
            ) : null}
          </div>
        </div>

        {leader.thesisClaims.length > 0 ? (
          <div className="prose-editorial mt-4">
            {leader.thesisClaims.slice(0, 2).map((c) => (
              <Claim key={c.claim_id} claim={c} />
            ))}
          </div>
        ) : (
          <p className="mt-4 text-sm italic text-unknown">
            No portfolio thesis claim survived evidence checks for this engineer.
          </p>
        )}

        {/* Strongest dimensions — bands, never scores. */}
        <div className="mt-4">
          <p className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Strongest dimensions</p>
          {strongest.length === 0 ? (
            <p className="text-xs italic text-unknown">No dimension was assessable for this engineer.</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {strongest.map((d) => (
                <li key={d.dimension}>
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2 py-1 text-xs">
                    <span
                      aria-hidden="true"
                      className="size-2 rounded-[2px]"
                      style={{ backgroundColor: dimensionColor(d.dimension) }}
                    />
                    <span className="font-medium text-ink">{dimensionLabel(d.dimension)}</span>
                    <span className="text-muted">— {bandName(d.value)}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <details className="group mt-4">
          <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
            <span aria-hidden="true" className="transition-transform group-open:rotate-90">
              ▸
            </span>
            All six dimensions
          </summary>
          <div className="mt-3">
            <DimensionBandChart profile={leader.dimensionProfile} compact highlight={leader.strongestDimension} />
          </div>
        </details>

        {/* Top evidence episodes. */}
        <div className="mt-5">
          <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Strongest evidence</p>
          {leader.topEpisodes.length === 0 ? (
            <EmptyState title="No episode was featured for this engineer.">
              Phase 2 published no strongest-evidence episode, so nothing is asserted here.
            </EmptyState>
          ) : (
            <ul className="space-y-2.5">
              {leader.topEpisodes.slice(0, 2).map((ep) => (
                <li key={ep.episodeId}>
                  <ImpactEpisodeCard episode={ep} compact />
                </li>
              ))}
            </ul>
          )}
        </div>

        {leader.incomparableWith.length > 0 ? (
          <p className="mt-4 flex items-start gap-2 rounded-lg border border-line bg-surface-sunken/60 p-3 text-xs leading-relaxed text-ink-soft">
            <Unlink aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted" />
            <span>
              <strong>Incomparable with {leader.incomparableWith.join(', ')}.</strong> The evidence does not settle
              which of them ranks higher — that is a result, not a tie-break failure.
            </span>
          </p>
        ) : null}

        {leader.rolesHeld.length > 0 ? (
          <ul className="mt-4 flex flex-wrap gap-1">
            {leader.rolesHeld.map((r) => (
              <li key={r}>
                <Badge tone="neutral">{roleLabel(r)}</Badge>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line bg-surface-sunken/40 px-5 py-3">
        <WhyThisRanking
          actorClusterId={leader.actorClusterId}
          login={leader.login}
          pairs={pairs}
          scenarioLabel={scenarioLabel}
        />
        <Link
          href={`/engineers/${leader.slug}/`}
          className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-xs font-semibold text-d2 hover:underline"
        >
          Full profile and evidence
          <ArrowRight aria-hidden="true" className="size-3.5" />
        </Link>
      </div>
    </article>
  );
}
