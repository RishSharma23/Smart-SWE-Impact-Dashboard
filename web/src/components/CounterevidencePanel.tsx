import { AlertOctagon } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import type { Counterevidence } from '@/lib/schema';
import { cn, humanize } from '@/lib/ui';

import { Badge, Callout } from './primitives';

/**
 * Contract §6.2: counterevidence is not optional chrome, and an entry with
 * `requires_human_confirmation: true` must never be called a regression — those
 * are deliberately low-precision recall signals from Phase 1.
 */
export function CounterevidencePanel({
  items,
  className,
  emptyNote,
}: {
  items: (Counterevidence & { episodeId?: string; episodeTitle?: string | null; slug?: string | null })[];
  className?: string;
  emptyNote?: string;
}) {
  if (items.length === 0) {
    return (
      <Callout tone="neutral" className={className}>
        <p>
          {emptyNote ??
            'No counterevidence was detected for this work in the public record. That is the absence of a signal, not a clean bill of health.'}
        </p>
      </Callout>
    );
  }

  const unconfirmed = items.filter((i) => i.requires_human_confirmation).length;

  return (
    <div className={cn('space-y-3', className)}>
      {unconfirmed > 0 ? (
        <Callout tone="warn" title={`${unconfirmed} of these ${unconfirmed === 1 ? 'signal is' : 'signals are'} unconfirmed`}>
          <p>
            Phase 1 casts a deliberately wide net for revert-like and fix-like events so that nothing is missed. A wide
            net catches ordinary follow-up work too. An unconfirmed signal is <strong>not</strong> a regression, and this
            dashboard does not call it one.
          </p>
        </Callout>
      ) : null}

      <ul className="space-y-2.5">
        {items.map((item, i) => (
          <li
            key={`${item.episodeId ?? ''}-${item.kind ?? ''}-${item.pr_number ?? i}`}
            className={cn(
              'rounded-lg border p-3.5',
              item.requires_human_confirmation ? 'border-[#d8b263] bg-[#fbf0d9]' : 'border-[#d99f9f] bg-[#f8e6e6]',
            )}
          >
            <div className="mb-1.5 flex flex-wrap items-center gap-2">
              <AlertOctagon
                aria-hidden="true"
                className={cn('size-4 shrink-0', item.requires_human_confirmation ? 'text-warn' : 'text-danger')}
              />
              <span className="text-sm font-semibold text-ink">{humanize(item.kind) || 'Counterevidence'}</span>
              {item.evidence_tier ? <Badge tone="outline">tier {item.evidence_tier}</Badge> : null}
              {item.requires_human_confirmation ? (
                <Badge tone="warn">unconfirmed — needs human review</Badge>
              ) : (
                <Badge tone="danger">confirmed</Badge>
              )}
            </div>
            {item.detail ? <p className="text-sm leading-relaxed text-ink-soft">{item.detail}</p> : null}
            <p className="mt-1.5 flex flex-wrap gap-x-3 text-xs text-muted">
              {item.pr_number ? <span className="font-mono">PR #{item.pr_number}</span> : null}
              {item.episodeTitle ? (
                item.slug ? (
                  <Link href={`/episodes/${item.slug}/`} className="underline underline-offset-2 hover:text-ink">
                    {item.episodeTitle}
                  </Link>
                ) : (
                  <span>{item.episodeTitle}</span>
                )
              ) : null}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
