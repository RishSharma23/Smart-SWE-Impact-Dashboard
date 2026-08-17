'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { CalendarRange, Info, Lock, SlidersHorizontal, X } from 'lucide-react';
import * as React from 'react';

import { cn, formatDate, dimensionLabel } from '@/lib/ui';

import { useScenario } from './ScenarioProvider';

/**
 * RankingScenarioSelector — available scenarios are buttons; unavailable ones
 * stay visible, disabled, with the reason and the exact remedy command.
 */
export function RankingScenarioSelector({ className }: { className?: string }) {
  const { scenarios, selected, select } = useScenario();
  return (
    <div className={cn('min-w-0', className)}>
      <div className="mb-1.5 flex items-center gap-1.5">
        <SlidersHorizontal aria-hidden="true" className="size-3.5 text-muted" />
        <span id="scenario-label" className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
          Ranking scenario
        </span>
      </div>
      <div role="radiogroup" aria-labelledby="scenario-label" className="flex flex-wrap gap-1.5">
        {scenarios.map((s) => {
          const isSelected = s.scenario === selected;
          if (!s.available) {
            return (
              <UnavailableScenario key={s.scenario} scenario={s} />
            );
          }
          return (
            <button
              key={s.scenario}
              type="button"
              role="radio"
              aria-checked={isSelected}
              onClick={() => select(s.scenario)}
              title={s.description ?? undefined}
              className={cn(
                'inline-flex min-h-9 items-center rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
                isSelected
                  ? 'border-accent bg-accent-wash text-accent-ink shadow-[inset_0_0_0_1px_var(--color-accent)]'
                  : 'border-line-strong bg-surface text-ink-soft hover:border-ink-soft hover:bg-surface-sunken',
              )}
            >
              {s.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function UnavailableScenario({ scenario }: { scenario: ReturnType<typeof useScenario>['scenarios'][number] }) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="inline-flex min-h-9 items-center gap-1 rounded-md border border-dashed border-line-strong bg-surface-sunken px-2.5 py-1 text-xs font-medium text-unknown line-through decoration-unknown/50 transition-colors hover:border-warn hover:text-warn"
        >
          <Lock aria-hidden="true" className="size-3" />
          {scenario.label}
          <span className="sr-only"> — unavailable, select to read why</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/35" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-line bg-surface p-6 shadow-2xl focus:outline-none">
          <div className="mb-3 flex items-start justify-between gap-4">
            <Dialog.Title className="text-base font-semibold text-ink">
              &ldquo;{scenario.label}&rdquo; could not be computed
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                className="-m-2 inline-flex size-11 items-center justify-center rounded-lg text-muted hover:bg-surface-sunken hover:text-ink"
              >
                <X aria-hidden="true" className="size-5" />
                <span className="sr-only">Close</span>
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="mb-4 text-sm leading-relaxed text-ink-soft">
            {scenario.unavailable_reason ??
              'Phase 2 marked this scenario unavailable but did not record a reason.'}
          </Dialog.Description>
          {scenario.remedy ? (
            <div>
              <p className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
                What would make it available
              </p>
              <pre className="overflow-x-auto rounded-lg border border-line bg-surface-sunken p-3 font-mono text-xs text-ink">
                {scenario.remedy}
              </pre>
            </div>
          ) : null}
          <p className="mt-4 text-xs leading-relaxed text-muted">
            This scenario is shown disabled rather than hidden. Silently falling back to the balanced ranking would hide
            the fact that a time horizon was not measurable.
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * AnalysisWindowSelector — the run analysed exactly one window. Longer windows
 * are offered and disabled, carrying the same reason as the scenarios that
 * would need them.
 */
export function AnalysisWindowSelector({
  start,
  end,
  lookbackDays,
  className,
}: {
  start: string;
  end: string;
  lookbackDays: number | null;
  className?: string;
}) {
  const { scenarios } = useScenario();
  const longer = scenarios.filter((s) => !s.available);

  return (
    <div className={cn('min-w-0', className)}>
      <div className="mb-1.5 flex items-center gap-1.5">
        <CalendarRange aria-hidden="true" className="size-3.5 text-muted" />
        <span id="window-label" className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
          Analysis window
        </span>
      </div>
      <div role="radiogroup" aria-labelledby="window-label" className="flex flex-wrap gap-1.5">
        <button
          type="button"
          role="radio"
          aria-checked="true"
          className="inline-flex min-h-9 items-center rounded-md border border-accent bg-accent-wash px-2.5 py-1 text-xs font-medium text-accent-ink shadow-[inset_0_0_0_1px_var(--color-accent)]"
        >
          {lookbackDays ? `${lookbackDays} days` : 'Analysed window'}
          <span className="ml-1.5 font-mono text-[10px] opacity-80">
            {formatDate(start)} – {formatDate(end)}
          </span>
        </button>
        {longer.map((s) => (
          <button
            key={s.scenario}
            type="button"
            role="radio"
            aria-checked="false"
            aria-disabled="true"
            disabled
            title={s.unavailable_reason ?? undefined}
            className="inline-flex min-h-9 cursor-not-allowed items-center gap-1 rounded-md border border-dashed border-line-strong bg-surface-sunken px-2.5 py-1 text-xs font-medium text-unknown"
          >
            <Lock aria-hidden="true" className="size-3" />
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Weights for the selected scenario — the contract requires exposing them. */
export function ScenarioWeights({ className }: { className?: string }) {
  const { meta } = useScenario();
  const weights = meta?.weights;
  if (!weights || Object.keys(weights).length === 0) return null;
  const entries = Object.entries(weights).sort((a, b) => b[1] - a[1]);
  return (
    <div className={cn('', className)}>
      <p className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
        <Info aria-hidden="true" className="size-3.5" />
        Criterion weights in &ldquo;{meta?.label}&rdquo;
      </p>
      <ul className="grid gap-1.5 sm:grid-cols-2">
        {entries.map(([dim, w]) => (
          <li key={dim} className="flex items-center gap-2 text-xs">
            <span className="min-w-0 flex-1 truncate text-ink-soft">{dimensionLabel(dim)}</span>
            <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-surface-sunken">
              <span
                className="block h-full rounded-full bg-ink-soft"
                style={{ width: `${Math.min(100, w * 300)}%` }}
                aria-hidden="true"
              />
            </span>
            <span className="w-10 shrink-0 text-right font-mono text-muted">{w.toFixed(2)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        Weights set how much a criterion counts toward a pairwise outranking decision. They are not multipliers on a
        score — there is no score.
      </p>
    </div>
  );
}
