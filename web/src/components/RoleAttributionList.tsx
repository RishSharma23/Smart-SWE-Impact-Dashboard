import Link from 'next/link';
import * as React from 'react';

import type { Claim as ClaimType, Participant } from '@/lib/schema';
import { cn, humanize, roleLabel, shareLabel } from '@/lib/ui';

import { Claim } from './Claim';
import { SourceLink } from './SourceLink';
import { Badge } from './primitives';

/**
 * Contract §6.3: share is a category, never a percentage, and `unclear` means
 * the evidence did not settle it — not "0%". Shared contributors are always
 * displayed; nothing here implies sole credit.
 */
export function RoleAttributionList({
  participants,
  claimsByActor,
  slugsByActor,
  className,
  highlightActor,
}: {
  participants: Participant[];
  claimsByActor?: Record<string, ClaimType[]>;
  slugsByActor?: Record<string, string>;
  className?: string;
  highlightActor?: string;
}) {
  if (participants.length === 0) {
    return (
      <p className={cn('text-sm italic text-unknown', className)}>
        No contributor could be attributed to this episode with direct evidence.
      </p>
    );
  }

  return (
    <div className={className}>
      <ul className="space-y-3">
        {participants.map((p) => {
          const slug = slugsByActor?.[p.actor_cluster_id];
          const claims = claimsByActor?.[p.actor_cluster_id] ?? [];
          return (
            <li
              key={p.actor_cluster_id}
              className={cn(
                'rounded-lg border p-3.5',
                highlightActor === p.actor_cluster_id ? 'border-accent bg-accent-wash' : 'border-line bg-surface',
              )}
            >
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                {slug ? (
                  <Link href={`/engineers/${slug}/`} className="font-mono text-sm font-semibold text-ink hover:underline">
                    @{p.login ?? p.actor_cluster_id}
                  </Link>
                ) : (
                  <span className="font-mono text-sm font-semibold text-ink">@{p.login ?? p.actor_cluster_id}</span>
                )}
                <Badge tone={p.share_category === 'primary' ? 'accent' : p.share_category === 'unclear' ? 'unknown' : 'neutral'}>
                  {shareLabel(p.share_category)}
                </Badge>
                {p.attribution_confidence ? (
                  <Badge tone={p.attribution_confidence === 'high' ? 'ok' : p.attribution_confidence === 'low' ? 'warn' : 'outline'}>
                    {humanize(p.attribution_confidence)} attribution confidence
                  </Badge>
                ) : null}
              </div>

              {p.roles.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-1">
                  {p.roles.map((r) => (
                    <li key={r}>
                      <Badge tone="outline">{roleLabel(r)}</Badge>
                    </li>
                  ))}
                </ul>
              ) : null}

              {p.share_reasons.length > 0 ? (
                <ul className="mt-2 space-y-0.5">
                  {p.share_reasons.map((r) => (
                    <li key={r} className="text-xs leading-relaxed text-ink-soft">
                      {r}
                    </li>
                  ))}
                </ul>
              ) : null}

              {claims.length > 0 ? (
                <div className="prose-editorial mt-2">
                  {claims.map((c) => (
                    <Claim key={c.claim_id} claim={c} tone="inline" />
                  ))}
                </div>
              ) : null}

              {p.direct_evidence.length > 0 ? (
                <ul className="mt-2.5 space-y-1.5 border-t border-line pt-2.5">
                  {p.direct_evidence.map((e, i) => (
                    <li key={`${e.artifact_id ?? i}`} className="text-xs leading-relaxed">
                      {e.role ? <Badge tone="neutral" className="mr-1.5">{roleLabel(e.role)}</Badge> : null}
                      <span className="text-ink-soft">{e.detail}</span>{' '}
                      {e.url ? (
                        <SourceLink href={e.url} className="text-[11px]">
                          {e.url.replace('https://github.com/PostHog/posthog/', '')}
                        </SourceLink>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs italic text-unknown">No direct artifact was attached to this attribution.</p>
              )}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Credit is a category, not a percentage. The export contains no split figure and the UI does not compute one —
        &ldquo;credit unclear&rdquo; means the evidence did not settle it, not that the contribution was zero.
      </p>
    </div>
  );
}
