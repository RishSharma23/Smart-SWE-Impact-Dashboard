import * as React from 'react';

import { cn } from '@/lib/ui';

/**
 * Tier is the primary grouping; position is a secondary detail. Two engineers
 * in the same tier are NOT distinguishable on this evidence, so the badge never
 * renders as a podium medal.
 */
export function ImpactTierBadge({
  tier,
  position,
  sharedWith = 0,
  className,
}: {
  tier: number;
  position?: number;
  sharedWith?: number;
  className?: string;
}) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <span
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-xs font-bold uppercase tracking-wider',
          tier === 1
            ? 'border-accent bg-accent-wash text-accent-ink'
            : tier === 2
              ? 'border-line-strong bg-surface-sunken text-ink-soft'
              : 'border-line bg-surface text-muted',
        )}
      >
        Tier {tier}
      </span>
      {position !== undefined ? (
        <span className="font-mono text-xs text-muted">
          position {position}
          {sharedWith > 0 ? (
            <span className="ml-1 normal-case">
              · shared tier
              <span className="sr-only">
                , not distinguishable from {sharedWith} other {sharedWith === 1 ? 'engineer' : 'engineers'}
              </span>
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
