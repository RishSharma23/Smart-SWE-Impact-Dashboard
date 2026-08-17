'use client';

import { ArrowUpDown, Minus } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import type { LeaderView, PairwiseView, ScenarioMovement } from '@/lib/viewmodel';
import { cn } from '@/lib/ui';

import { LeaderCard } from './LeaderCard';
import { ScenarioWeights } from './Selectors';
import { useScenario } from './ScenarioProvider';
import { DataTable, EmptyState, SectionHeading, TableScroll, Td, Th } from './primitives';

/**
 * The ranked cards for the selected scenario. Switching scenarios re-renders in
 * place with a short crossfade and announces the change to screen readers via
 * ScenarioProvider's live region.
 */
export function OverviewLeaders({
  leadersByScenario,
  pairwiseByScenario,
}: {
  leadersByScenario: Record<string, LeaderView[]>;
  pairwiseByScenario: Record<string, PairwiseView[]>;
}) {
  const { selected, meta } = useScenario();
  const leaders = leadersByScenario[selected] ?? [];
  const pairs = pairwiseByScenario[selected] ?? [];

  return (
    <section aria-labelledby="leaders-heading">
      <SectionHeading
        id="leaders-heading"
        eyebrow={`${meta?.label ?? selected} scenario`}
        title="The five strongest observable-impact profiles"
        description={
          <>
            Grouped by tier. Engineers in the same tier are <strong>not distinguishable</strong> on this evidence —
            position inside a tier is a detail, not a verdict.
            {meta?.alternatives ? (
              <>
                {' '}
                {meta.alternatives} engineers were rankable
                {meta.excluded_insufficient_evidence
                  ? `; ${meta.excluded_insufficient_evidence} were excluded for insufficient observable evidence`
                  : ''}
                .
              </>
            ) : null}
          </>
        }
      />

      {meta?.note ? (
        <p className="mb-4 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-muted">{meta.note}</p>
      ) : null}

      {leaders.length === 0 ? (
        <EmptyState title="This scenario produced no ranked engineers.">
          Nothing is invented to fill the space.
        </EmptyState>
      ) : (
        <div key={selected} className="animate-fade-rise grid gap-4 lg:grid-cols-2">
          {leaders.map((leader) => (
            <LeaderCard
              key={leader.actorClusterId}
              leader={leader}
              pairs={pairs}
              scenarioLabel={meta?.label ?? selected}
            />
          ))}
        </div>
      )}

      <div className="mt-6 rounded-lg border border-line bg-surface p-4">
        <ScenarioWeights />
      </div>
    </section>
  );
}

/** Do the same names appear, and do they move? */
export function ScenarioComparison({ movement }: { movement: { scenarios: string[]; rows: ScenarioMovement[] } }) {
  const { scenarios, select, selected } = useScenario();
  const labelOf = (name: string) => scenarios.find((s) => s.scenario === name)?.label ?? name;

  if (movement.scenarios.length < 2) {
    return (
      <section aria-labelledby="movement-heading">
        <SectionHeading
          id="movement-heading"
          title="Does the ranking survive a different weighting?"
          description="Only one scenario is available in this run, so there is nothing to compare against yet. The disabled scenarios in the header explain what would be needed."
        />
      </section>
    );
  }

  const stable = movement.rows.filter((r) => !r.moves).length;

  return (
    <section aria-labelledby="movement-heading">
      <SectionHeading
        id="movement-heading"
        title="Does the ranking survive a different weighting?"
        description={
          <>
            {stable} of {movement.rows.length} engineers hold the same position across every available scenario. Where a
            name moves, the weighting it prefers is the interesting part — not the movement itself.
          </>
        }
      />
      <TableScroll label="Positions across ranking scenarios">
        <DataTable>
          <caption className="sr-only">
            Each engineer&apos;s position and tier in every available ranking scenario. A dash means they were outside
            the top five in that scenario.
          </caption>
          <thead>
            <tr>
              <Th>Engineer</Th>
              {movement.scenarios.map((s) => (
                <Th key={s}>
                  <button
                    type="button"
                    onClick={() => select(s)}
                    className={cn(
                      'min-h-11 rounded px-1 text-left uppercase tracking-[0.1em]',
                      s === selected ? 'text-accent-ink underline decoration-accent' : 'hover:text-ink',
                    )}
                  >
                    {labelOf(s)}
                  </button>
                </Th>
              ))}
              <Th>Movement</Th>
            </tr>
          </thead>
          <tbody>
            {movement.rows.map((row) => (
              <tr key={row.actorClusterId}>
                <Th scope="row" className="bg-surface normal-case tracking-normal">
                  <Link href={`/engineers/${row.slug}/`} className="font-medium text-ink hover:underline">
                    {row.login}
                  </Link>
                </Th>
                {movement.scenarios.map((s) => {
                  const cell = row.byScenario[s];
                  return (
                    <Td key={s} className="font-mono">
                      {cell ? (
                        <>
                          <span className="text-ink">#{cell.position}</span>{' '}
                          <span className="text-muted">tier {cell.tier}</span>
                        </>
                      ) : (
                        <span className="text-unknown" title="Outside the top five in this scenario">
                          —
                        </span>
                      )}
                    </Td>
                  );
                })}
                <Td>
                  {row.moves ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-warn">
                      <ArrowUpDown aria-hidden="true" className="size-3.5" />
                      moves
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-ok">
                      <Minus aria-hidden="true" className="size-3.5" />
                      holds
                    </span>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </TableScroll>
    </section>
  );
}
