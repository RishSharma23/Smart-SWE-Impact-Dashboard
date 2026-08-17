import type { Metadata } from 'next';
import Link from 'next/link';
import * as React from 'react';

import { Badge, Card, EmptyState, SectionHeading, TableScroll, DataTable, Td, Th } from '@/components/primitives';
import { defaultScenario, loadBundle, slugFor } from '@/lib/data';
import { bandName, dimensionLabel, formatDate, formatNumber, roleLabel } from '@/lib/ui';

export const metadata: Metadata = {
  title: 'Engineers',
  description: 'Every contributor the analysis saw, ranked and unranked, with the reason each one is where it is.',
};

export default function EngineersPage() {
  const bundle = loadBundle();
  const scenario = defaultScenario(bundle);
  const positionByActor = new Map(scenario.positions.map((p) => [p.actor_cluster_id, p]));

  const rankable = bundle.engineers.filter((e) => e.rankable);
  const unrankable = bundle.engineers.filter((e) => !e.rankable);

  return (
    <div className="space-y-10">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold leading-tight text-ink">Contributors</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">
          Every contributor with attributed episodes in the analysed window. Positions shown are for the{' '}
          <strong>{scenario.label ?? scenario.scenario}</strong> scenario; use the header selector to change it, or open
          a profile to see how the position moves across all scenarios.
        </p>
      </header>

      <section aria-labelledby="rankable-heading">
        <SectionHeading
          id="rankable-heading"
          title={`Rankable contributors (${formatNumber(rankable.length)})`}
          description="Enough observable evidence across enough dimensions to be compared pairwise."
        />
        {rankable.length === 0 ? (
          <EmptyState title="No contributor cleared the evidence bar in this run." />
        ) : (
          <TableScroll label="Rankable contributors">
            <DataTable>
              <caption className="sr-only">
                Rankable contributors with their position and tier in the {scenario.label ?? scenario.scenario}{' '}
                scenario, strongest dimension, roles and episode count.
              </caption>
              <thead>
                <tr>
                  <Th>Contributor</Th>
                  <Th>Position</Th>
                  <Th>Tier</Th>
                  <Th>Strongest dimension</Th>
                  <Th>Roles observed</Th>
                  <Th>Episodes</Th>
                  <Th>Active</Th>
                </tr>
              </thead>
              <tbody>
                {rankable
                  .slice()
                  .sort((a, b) => {
                    const pa = positionByActor.get(a.actor_cluster_id)?.position ?? 9999;
                    const pb = positionByActor.get(b.actor_cluster_id)?.position ?? 9999;
                    return pa - pb || (a.login ?? '').localeCompare(b.login ?? '');
                  })
                  .map((e) => {
                    const pos = positionByActor.get(e.actor_cluster_id);
                    const strongest = e.dimension_profile.find((d) => d.dimension === e.strongest_dimension);
                    return (
                      <tr key={e.actor_cluster_id}>
                        <Th scope="row" className="bg-surface normal-case tracking-normal">
                          <Link
                            href={`/engineers/${slugFor(e.actor_cluster_id)}/`}
                            className="font-mono font-medium text-ink hover:underline"
                          >
                            @{e.login ?? e.actor_cluster_id}
                          </Link>
                        </Th>
                        <Td className="font-mono">{pos ? `#${pos.position}` : <span className="text-unknown">outside</span>}</Td>
                        <Td className="font-mono">{pos ? pos.tier : '—'}</Td>
                        <Td>
                          {e.strongest_dimension ? (
                            <>
                              {dimensionLabel(e.strongest_dimension)}
                              {strongest && !strongest.is_unknown && strongest.value !== null ? (
                                <span className="text-muted"> — {bandName(strongest.value)}</span>
                              ) : null}
                            </>
                          ) : (
                            <span className="italic text-unknown">none assessable</span>
                          )}
                        </Td>
                        <Td className="text-xs">{e.roles_held.map(roleLabel).join(', ') || '—'}</Td>
                        <Td className="font-mono">{formatNumber(e.episode_count ?? e.episode_ids.length)}</Td>
                        <Td className="whitespace-nowrap font-mono text-xs">
                          {formatDate(e.active_period?.first_observed)} → {formatDate(e.active_period?.last_observed)}
                        </Td>
                      </tr>
                    );
                  })}
              </tbody>
            </DataTable>
          </TableScroll>
        )}
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Episode counts describe coverage. They are not a productivity measure and are not used to rank anyone — ten
          small episodes do not outrank one strong one.
        </p>
      </section>

      <section aria-labelledby="unrankable-list-heading">
        <SectionHeading
          id="unrankable-list-heading"
          title={`Insufficient observable evidence (${formatNumber(unrankable.length)})`}
          description="Excluded from every ranking. This is a statement about what a 90-day public window can show, not about the person."
        />
        {unrankable.length === 0 ? (
          <EmptyState title="No contributor was excluded in this run." />
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {unrankable.map((e) => (
              <li key={e.actor_cluster_id}>
                <Card className="h-full p-4">
                  <div className="flex items-center justify-between gap-2">
                    <Link
                      href={`/engineers/${slugFor(e.actor_cluster_id)}/`}
                      className="truncate font-mono text-sm font-medium text-ink hover:underline"
                    >
                      @{e.login ?? e.actor_cluster_id}
                    </Link>
                    <Badge tone="unknown">{e.eligibility_label ?? 'not ranked'}</Badge>
                  </div>
                  {e.eligibility_reasons.length > 0 ? (
                    <ul className="mt-2 space-y-1">
                      {e.eligibility_reasons.map((r) => (
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
      </section>
    </div>
  );
}
