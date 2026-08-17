import { AlertOctagon, ArrowRight, GitPullRequest, Users } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import type { EpisodeSummary } from '@/lib/viewmodel';
import { cn, formatDate, REACHABILITY_LABEL, roleLabel, shareLabel, statusLabel } from '@/lib/ui';

import { Claim } from './Claim';
import { Badge } from './primitives';

/**
 * Contract §6.1: `release_corroboration` sits next to `status`, always. A
 * `merged_only` episode says "merged; release not independently corroborated" —
 * the UI must not visually promote it to "shipped to users".
 */
export function ReleaseQualifier({ value, className }: { value: string | null; className?: string }) {
  if (value === 'corroborated') {
    return (
      <Badge tone="ok" className={className} title="A second, independent artifact class confirms the release.">
        Release corroborated
      </Badge>
    );
  }
  return (
    <Badge
      tone="warn"
      className={className}
      title="The change landed on the default branch. Nothing independently confirms that users saw it."
    >
      Merged; release not corroborated
    </Badge>
  );
}

export function ImpactEpisodeCard({
  episode,
  className,
  showParticipants = true,
  compact = false,
}: {
  episode: EpisodeSummary;
  className?: string;
  showParticipants?: boolean;
  compact?: boolean;
}) {
  const href = episode.slug ? `/episodes/${episode.slug}/` : null;

  return (
    <article className={cn('card card-interactive p-4', className)}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <Badge tone="outline">{statusLabel(episode.status)}</Badge>
        <ReleaseQualifier value={episode.releaseCorroboration} />
        {episode.shareCategory ? (
          <Badge tone={episode.shareCategory === 'primary' ? 'accent' : 'neutral'}>
            {shareLabel(episode.shareCategory)}
          </Badge>
        ) : null}
        {episode.counterevidenceCount > 0 ? (
          <Badge tone="danger">
            <AlertOctagon aria-hidden="true" className="size-3" />
            {episode.counterevidenceCount} counterevidence
            {episode.requiresHumanConfirmation ? ' (unconfirmed)' : ''}
          </Badge>
        ) : null}
      </div>

      <h3 className="text-[15px] font-semibold leading-snug text-ink">
        {href ? (
          <Link href={href} className="hover:underline hover:decoration-accent hover:underline-offset-4">
            {episode.title ?? 'Untitled episode'}
          </Link>
        ) : (
          (episode.title ?? 'Untitled episode')
        )}
      </h3>

      {!compact && episode.titleClaim ? (
        <Claim claim={episode.titleClaim} className="mt-1.5" tone="inline" />
      ) : null}

      <dl className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
        <div className="flex items-center gap-1.5">
          <dt className="sr-only">Dates</dt>
          <dd className="font-mono">
            {formatDate(episode.startedAt)} → {formatDate(episode.endedAt)}
          </dd>
        </div>
        {episode.prNumbers.length > 0 ? (
          <div className="flex items-center gap-1.5">
            <dt>
              <GitPullRequest aria-hidden="true" className="size-3.5" />
              <span className="sr-only">Pull requests</span>
            </dt>
            <dd className="font-mono">{episode.prNumbers.map((n) => `#${n}`).join(' ')}</dd>
          </div>
        ) : null}
        {showParticipants && episode.participantCount > 0 ? (
          <div className="flex items-center gap-1.5">
            <dt>
              <Users aria-hidden="true" className="size-3.5" />
              <span className="sr-only">Contributors</span>
            </dt>
            <dd>
              {episode.participantCount} {episode.participantCount === 1 ? 'contributor' : 'contributors'}
            </dd>
          </div>
        ) : null}
        {episode.reachabilityBand ? (
          <div className="flex items-center gap-1.5">
            <dt className="sr-only">Reach</dt>
            <dd>{REACHABILITY_LABEL[episode.reachabilityBand] ?? episode.reachabilityBand}</dd>
          </div>
        ) : null}
      </dl>

      {episode.roles.length > 0 ? (
        <ul className="mt-2.5 flex flex-wrap gap-1">
          {episode.roles.map((r) => (
            <li key={r}>
              <Badge tone="neutral">{roleLabel(r)}</Badge>
            </li>
          ))}
        </ul>
      ) : null}

      {episode.components.length > 0 ? (
        <p className="mt-2.5 truncate font-mono text-[11px] text-muted">{episode.components.join(' · ')}</p>
      ) : null}

      {href ? (
        <p className="mt-3">
          <Link
            href={href}
            className="inline-flex min-h-11 items-center gap-1 text-xs font-semibold text-d2 hover:underline"
          >
            Problem, intervention, result and sources
            <ArrowRight aria-hidden="true" className="size-3.5" />
          </Link>
        </p>
      ) : null}
    </article>
  );
}
