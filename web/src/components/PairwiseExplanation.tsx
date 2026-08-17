'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { GitCompareArrows, X } from 'lucide-react';
import * as React from 'react';

import type { PairwiseView } from '@/lib/viewmodel';
import { cn, dimensionLabel, formatPercent } from '@/lib/ui';

import { Claim } from './Claim';
import { DataTable, TableScroll, Td, Th, Badge, EmptyState } from './primitives';

/**
 * "Why is A above B?" — the published methodology trace for one ordered pair.
 * Excluded criteria are the point: they are where "unknown is not zero" becomes
 * visible, so they are never collapsed away.
 */
export function PairwiseExplanation({ pair, className }: { pair: PairwiseView; className?: string }) {
  return (
    <div className={cn('space-y-4', className)}>
      <Claim claim={pair.explanationClaim} tone="lead" />

      <dl className="flex flex-wrap gap-x-6 gap-y-2">
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Concordance</dt>
          <dd className="font-mono text-sm text-ink">{formatPercent(pair.concordance, 1)}</dd>
        </div>
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Credibility</dt>
          <dd className="font-mono text-sm text-ink">{formatPercent(pair.credibility, 1)}</dd>
        </div>
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Counterevidence veto</dt>
          <dd className="text-sm text-ink">{pair.counterevidence_veto ? 'yes' : 'no'}</dd>
        </div>
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Vetoing criteria</dt>
          <dd className="text-sm text-ink">{pair.vetoing_criteria.length || 'none'}</dd>
        </div>
      </dl>

      <p className="text-xs leading-relaxed text-muted">
        Concordance is the weighted share of criteria on which{' '}
        <strong className="text-ink-soft">{pair.a_login ?? 'A'}</strong> is at least as good as{' '}
        <strong className="text-ink-soft">{pair.b_login ?? 'B'}</strong>. Credibility discounts it wherever a single
        criterion opposes strongly enough to veto. Neither is a score, and neither is comparable across pairs.
      </p>

      <TableScroll label={`Per-criterion comparison of ${pair.a_login} and ${pair.b_login}`}>
        <DataTable>
          <caption className="sr-only">
            Per-criterion concordance and discordance between {pair.a_login} and {pair.b_login}.
          </caption>
          <thead>
            <tr>
              <Th>Criterion</Th>
              <Th>{pair.a_login ?? 'A'}</Th>
              <Th>{pair.b_login ?? 'B'}</Th>
              <Th>Difference</Th>
              <Th>Weight</Th>
              <Th>Concordance</Th>
              <Th>Discordance</Th>
            </tr>
          </thead>
          <tbody>
            {pair.per_criterion.map((c) => (
              <tr key={c.criterion}>
                <Th scope="row" className="bg-surface normal-case tracking-normal text-ink">
                  {dimensionLabel(c.criterion)}
                </Th>
                <Td className="font-mono">{c.a_value === null ? 'n/a' : c.a_value.toFixed(2)}</Td>
                <Td className="font-mono">{c.b_value === null ? 'n/a' : c.b_value.toFixed(2)}</Td>
                <Td className={cn('font-mono', (c.difference ?? 0) > 0 ? 'text-ok' : (c.difference ?? 0) < 0 ? 'text-danger' : '')}>
                  {c.difference === null || c.difference === undefined
                    ? '—'
                    : `${c.difference > 0 ? '+' : ''}${c.difference.toFixed(2)}`}
                </Td>
                <Td className="font-mono">{c.weight?.toFixed(2) ?? '—'}</Td>
                <Td className="font-mono">{c.concordance?.toFixed(2) ?? '—'}</Td>
                <Td className="font-mono">{c.discordance?.toFixed(2) ?? '—'}</Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </TableScroll>

      <div>
        <h4 className="mb-2 text-sm font-semibold text-ink">Criteria excluded from this comparison</h4>
        {pair.excluded_criteria.length === 0 ? (
          <p className="text-xs text-muted">
            None — every criterion was assessable for both engineers, so all six were scored.
          </p>
        ) : (
          <ul className="space-y-2">
            {pair.excluded_criteria.map((e) => (
              <li key={e.criterion} className="rounded-lg border border-line bg-surface-sunken p-3">
                <p className="text-sm font-medium text-ink">{dimensionLabel(e.criterion)}</p>
                {e.reason ? <p className="mt-0.5 text-xs text-ink-soft">{e.reason}</p> : null}
                <dl className="mt-1.5 space-y-0.5 text-xs text-muted">
                  {e.a_unknown_reason ? (
                    <div>
                      <dt className="inline font-medium">{pair.a_login}: </dt>
                      <dd className="inline">{e.a_unknown_reason}</dd>
                    </div>
                  ) : null}
                  {e.b_unknown_reason ? (
                    <div>
                      <dt className="inline font-medium">{pair.b_login}: </dt>
                      <dd className="inline">{e.b_unknown_reason}</dd>
                    </div>
                  ) : null}
                </dl>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs leading-relaxed text-muted">
          An excluded criterion was <strong>unknown for at least one side</strong>. It is dropped from the comparison
          rather than scored as zero — treating an unmeasured dimension as the worst possible value would invert its
          meaning.
        </p>
      </div>
    </div>
  );
}

/** "Why this ranking?" — opens every pairwise trace involving this engineer. */
export function WhyThisRanking({
  actorClusterId,
  login,
  pairs,
  scenarioLabel,
  className,
  variant = 'button',
}: {
  actorClusterId: string;
  login: string;
  pairs: PairwiseView[];
  scenarioLabel: string;
  className?: string;
  variant?: 'button' | 'link';
}) {
  const mine = pairs.filter((p) => p.a === actorClusterId);
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className={cn(
            variant === 'button'
              ? 'inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-xs font-semibold text-ink-soft transition-colors hover:border-accent hover:bg-accent-wash hover:text-accent-ink'
              : 'inline-flex items-center gap-1.5 text-xs font-medium text-d2 underline underline-offset-2',
            className,
          )}
        >
          <GitCompareArrows aria-hidden="true" className="size-3.5" />
          Why this ranking?
          <span className="sr-only"> for {login}</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/35" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(56rem,calc(100vw-1.5rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-line bg-surface p-5 shadow-2xl focus:outline-none sm:p-6">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-lg font-semibold text-ink">
                Why {login} sits where they do
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-ink-soft">
                Every ordered pair against the rest of the top five in the{' '}
                <strong>{scenarioLabel}</strong> scenario. There is no composite score to point at — the position is the
                result of these pairwise outranking relations.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="-m-2 inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-surface-sunken hover:text-ink"
              >
                <X aria-hidden="true" className="size-5" />
                <span className="sr-only">Close</span>
              </button>
            </Dialog.Close>
          </div>

          {mine.length === 0 ? (
            <EmptyState title="No pairwise material was published for this engineer in this scenario.">
              comparisons.json carries pairs only for the top five; this engineer is outside it here.
            </EmptyState>
          ) : (
            <div className="space-y-6">
              {mine.map((pair) => (
                <section key={`${pair.a}-${pair.b}`} className="rounded-lg border border-line p-4">
                  <h3 className="mb-3 flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                    <span>{pair.a_login}</span>
                    <Badge tone="outline">compared with</Badge>
                    <span>{pair.b_login}</span>
                  </h3>
                  <PairwiseExplanation pair={pair} />
                </section>
              ))}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
