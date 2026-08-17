'use client';

import { ArrowLeftRight } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import type { LeaderView, PairwiseView } from '@/lib/viewmodel';
import { bandName, cn, dimensionColor, dimensionLabel, roleLabel, sortDimensions, BAND_SCALE_MAX } from '@/lib/ui';

import { Avatar } from './Avatar';
import { Claim } from './Claim';
import { ImpactEpisodeCard } from './ImpactEpisodeCard';
import { ImpactTierBadge } from './ImpactTierBadge';
import { PairwiseExplanation } from './PairwiseExplanation';
import { StabilityIndicator } from './StabilityIndicator';
import { useScenario } from './ScenarioProvider';
import { Badge, Card, DataTable, EmptyState, SectionHeading, TableScroll, Td, Th } from './primitives';

/**
 * Two candidates, side by side. Deliberately absent: lines of code, commit
 * totals, pull-request totals and any notion of "productivity" — none of which
 * exist in the package and none of which the UI may synthesise.
 */
export function CompareView({
  leadersByScenario,
  pairwiseByScenario,
}: {
  leadersByScenario: Record<string, LeaderView[]>;
  pairwiseByScenario: Record<string, PairwiseView[]>;
}) {
  const { selected, meta } = useScenario();
  const leaders = leadersByScenario[selected] ?? [];
  const pairs = pairwiseByScenario[selected] ?? [];

  const [aId, setAId] = React.useState<string>('');
  const [bId, setBId] = React.useState<string>('');

  // Keep the selection valid when the scenario changes.
  React.useEffect(() => {
    const ids = leaders.map((l) => l.actorClusterId);
    setAId((prev) => (ids.includes(prev) ? prev : (ids[0] ?? '')));
    setBId((prev) => (ids.includes(prev) && prev !== (ids[0] ?? '') ? prev : (ids[1] ?? '')));
  }, [selected, leadersByScenario]); // eslint-disable-line react-hooks/exhaustive-deps

  const a = leaders.find((l) => l.actorClusterId === aId);
  const b = leaders.find((l) => l.actorClusterId === bId);

  const abPair = pairs.find((p) => p.a === aId && p.b === bId);
  const baPair = pairs.find((p) => p.a === bId && p.b === aId);

  if (leaders.length < 2) {
    return (
      <EmptyState title="This scenario ranked fewer than two engineers.">
        There is nothing to compare. Try another scenario from the header.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-8">
      <Card>
        <div className="grid items-end gap-4 sm:grid-cols-[1fr_auto_1fr]">
          <Picker label="First candidate" value={aId} onChange={setAId} leaders={leaders} exclude={bId} id="cmp-a" />
          <div className="hidden justify-center pb-2 sm:flex">
            <button
              type="button"
              onClick={() => {
                const prev = aId;
                setAId(bId);
                setBId(prev);
              }}
              className="inline-flex size-11 items-center justify-center rounded-lg border border-line-strong bg-surface text-muted transition-colors hover:border-accent hover:bg-accent-wash hover:text-accent-ink"
            >
              <ArrowLeftRight aria-hidden="true" className="size-4" />
              <span className="sr-only">Swap the two candidates</span>
            </button>
          </div>
          <Picker label="Second candidate" value={bId} onChange={setBId} leaders={leaders} exclude={aId} id="cmp-b" />
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted">
          Comparing within the <strong>{meta?.label ?? selected}</strong> scenario. Change the scenario in the header to
          see whether the relation reverses.
        </p>
      </Card>

      {!a || !b ? (
        <EmptyState title="Pick two candidates to compare." />
      ) : (
        <>
          <section aria-labelledby="cmp-heads">
            <h2 id="cmp-heads" className="sr-only">
              Candidate summaries
            </h2>
            <div className="grid gap-4 sm:grid-cols-2">
              {[a, b].map((c) => (
                <Card key={c.actorClusterId}>
                  <div className="flex items-start gap-3">
                    <Avatar src={c.avatarUrl} login={c.login} size={44} />
                    <div className="min-w-0">
                      <h3 className="truncate text-base font-semibold text-ink">
                        <Link href={`/engineers/${c.slug}/`} className="hover:underline">
                          {c.displayName}
                        </Link>
                      </h3>
                      <p className="truncate font-mono text-xs text-muted">@{c.login}</p>
                    </div>
                  </div>
                  <div className="mt-3">
                    <ImpactTierBadge tier={c.tier} position={c.position} sharedWith={c.sharedTierWith.length} />
                  </div>
                  {c.thesisClaims[0] ? <Claim claim={c.thesisClaims[0]} className="mt-3" /> : null}
                  <div className="mt-3">
                    <StabilityIndicator stability={c.stability} crossCheckDelta={c.crossCheckDelta} compact />
                  </div>
                  {c.rolesHeld.length > 0 ? (
                    <ul className="mt-3 flex flex-wrap gap-1">
                      {c.rolesHeld.map((r) => (
                        <li key={r}>
                          <Badge tone="neutral">{roleLabel(r)}</Badge>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </Card>
              ))}
            </div>
          </section>

          <section aria-labelledby="cmp-dims">
            <SectionHeading
              id="cmp-dims"
              title="Dimension by dimension"
              description="Opposed bars, on a shared 0–4 band scale. A hatched half means that dimension was not assessable for that engineer — it is dropped from the comparison, never counted as zero."
            />
            <Card>
              <OpposedBars a={a} b={b} />
            </Card>
          </section>

          <section aria-labelledby="cmp-why">
            <SectionHeading
              id="cmp-why"
              title="Why one outranks the other"
              description="The published methodology trace for both ordered directions. Outranking is not symmetric: A may outrank B without B failing to outrank A, and when neither does, they are incomparable."
            />
            <div className="space-y-5">
              {abPair ? (
                <Card>
                  <h3 className="mb-3 text-sm font-semibold text-ink">
                    Does {a.login} outrank {b.login}?
                  </h3>
                  <PairwiseExplanation pair={abPair} />
                </Card>
              ) : null}
              {baPair ? (
                <Card>
                  <h3 className="mb-3 text-sm font-semibold text-ink">
                    Does {b.login} outrank {a.login}?
                  </h3>
                  <PairwiseExplanation pair={baPair} />
                </Card>
              ) : null}
              {!abPair && !baPair ? (
                <EmptyState title="No pairwise material was published for this pair in this scenario.">
                  comparisons.json carries the pairs among the top five only.
                </EmptyState>
              ) : null}
              {a.incomparableWith.includes(b.login) ? (
                <Card className="border-accent bg-accent-wash">
                  <p className="text-sm leading-relaxed text-ink">
                    <strong>These two are incomparable in this scenario.</strong> Neither outranking relation holds with
                    enough credibility. The evidence does not settle which of them ranks higher, and forcing an order
                    would be an invention rather than a finding.
                  </p>
                </Card>
              ) : null}
            </div>
          </section>

          <section aria-labelledby="cmp-episodes">
            <SectionHeading
              id="cmp-episodes"
              title="Strongest episodes side by side"
              description="Read the evidence, not the ordering."
            />
            <div className="grid gap-4 sm:grid-cols-2">
              {[a, b].map((c) => (
                <div key={c.actorClusterId}>
                  <h3 className="mb-2.5 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">@{c.login}</h3>
                  {c.topEpisodes.length === 0 ? (
                    <EmptyState title="No featured episode." />
                  ) : (
                    <ul className="space-y-3">
                      {c.topEpisodes.map((ep) => (
                        <li key={ep.episodeId}>
                          <ImpactEpisodeCard episode={ep} compact />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section aria-labelledby="cmp-not">
            <SectionHeading id="cmp-not" title="What is deliberately not compared" />
            <Card>
              <ul className="grid gap-2 text-sm text-ink-soft sm:grid-cols-2">
                {[
                  'Lines of code',
                  'Commit totals',
                  'Pull-request totals',
                  'Review counts',
                  'Velocity or throughput ratios',
                  'Any composite "impact score"',
                ].map((x) => (
                  <li key={x} className="flex items-baseline gap-2">
                    <span aria-hidden="true" className="text-danger">
                      ✕
                    </span>
                    <span className="line-through decoration-line-strong">{x}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs leading-relaxed text-muted">
                None of these appear in the data package, and the UI does not derive them. They measure activity, and
                activity is not impact — especially in a repository with widespread AI-assisted authorship.
              </p>
            </Card>
          </section>
        </>
      )}
    </div>
  );
}

function Picker({
  label,
  value,
  onChange,
  leaders,
  exclude,
  id,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  leaders: LeaderView[];
  exclude: string;
  id: string;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1.5 block font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="min-h-11 w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink"
      >
        {leaders.map((l) => (
          <option key={l.actorClusterId} value={l.actorClusterId} disabled={l.actorClusterId === exclude}>
            #{l.position} · tier {l.tier} · @{l.login}
          </option>
        ))}
      </select>
    </div>
  );
}

function OpposedBars({ a, b }: { a: LeaderView; b: LeaderView }) {
  const dims = sortDimensions(a.dimensionProfile.map((d) => ({ dimension: d.dimension })));
  const valueOf = (leader: LeaderView, dim: string) => {
    const row = leader.dimensionProfile.find((d) => d.dimension === dim);
    if (!row || row.is_unknown || row.value === null) return null;
    return row.value;
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between text-xs font-semibold text-ink">
        <span>@{a.login}</span>
        <span className="font-mono text-[10px] uppercase tracking-wide text-muted">band 4 ← 0 → band 4</span>
        <span>@{b.login}</span>
      </div>

      <ul className="space-y-3">
        {dims.map(({ dimension }) => {
          const av = valueOf(a, dimension);
          const bv = valueOf(b, dimension);
          const color = dimensionColor(dimension);
          return (
            <li key={dimension}>
              <p className="mb-1 text-center text-xs font-medium text-ink">{dimensionLabel(dimension)}</p>
              <div
                role="img"
                aria-label={`${dimensionLabel(dimension)}: ${a.login} ${
                  av === null ? 'not assessable' : `band ${av.toFixed(2)}, ${bandName(av)}`
                }; ${b.login} ${bv === null ? 'not assessable' : `band ${bv.toFixed(2)}, ${bandName(bv)}`}.`}
                className="flex items-center gap-2"
              >
                <span className="w-14 shrink-0 text-right font-mono text-[11px] text-muted">
                  {av === null ? 'n/a' : av.toFixed(2)}
                </span>
                <span className="flex h-4 flex-1 justify-end overflow-hidden rounded-l-full border border-line bg-surface-sunken">
                  {av === null ? (
                    <span className="hatch h-full w-full opacity-70" />
                  ) : (
                    <span
                      className="h-full rounded-l-full"
                      style={{ backgroundColor: color, width: `${(av / BAND_SCALE_MAX) * 100}%` }}
                    />
                  )}
                </span>
                <span className="flex h-4 flex-1 overflow-hidden rounded-r-full border border-line bg-surface-sunken">
                  {bv === null ? (
                    <span className="hatch h-full w-full opacity-70" />
                  ) : (
                    <span
                      className="h-full rounded-r-full"
                      style={{ backgroundColor: color, width: `${(bv / BAND_SCALE_MAX) * 100}%`, opacity: 0.72 }}
                    />
                  )}
                </span>
                <span className="w-14 shrink-0 font-mono text-[11px] text-muted">
                  {bv === null ? 'n/a' : bv.toFixed(2)}
                </span>
              </div>
              {av === null || bv === null ? (
                <p className="mt-0.5 text-center text-[11px] italic text-unknown">
                  Not assessable for {av === null ? `@${a.login}` : ''}
                  {av === null && bv === null ? ' and ' : ''}
                  {bv === null ? `@${b.login}` : ''} — excluded from the comparison, not scored as zero.
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>

      <details className="group mt-4">
        <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
          <span aria-hidden="true" className="transition-transform group-open:rotate-90">
            ▸
          </span>
          Same comparison as a table
        </summary>
        <div className="mt-3">
          <TableScroll label="Dimension comparison table">
            <DataTable>
              <thead>
                <tr>
                  <Th>Dimension</Th>
                  <Th>@{a.login}</Th>
                  <Th>@{b.login}</Th>
                  <Th>Difference</Th>
                </tr>
              </thead>
              <tbody>
                {dims.map(({ dimension }) => {
                  const av = valueOf(a, dimension);
                  const bv = valueOf(b, dimension);
                  return (
                    <tr key={dimension}>
                      <Th scope="row" className="bg-surface normal-case tracking-normal text-ink">
                        {dimensionLabel(dimension)}
                      </Th>
                      <Td className="font-mono">{av === null ? 'not assessable' : `${av.toFixed(2)} (${bandName(av)})`}</Td>
                      <Td className="font-mono">{bv === null ? 'not assessable' : `${bv.toFixed(2)} (${bandName(bv)})`}</Td>
                      <Td className="font-mono">
                        {av === null || bv === null ? (
                          <span className="text-unknown">excluded</span>
                        ) : (
                          `${av - bv > 0 ? '+' : ''}${(av - bv).toFixed(2)}`
                        )}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </DataTable>
          </TableScroll>
        </div>
      </details>
    </div>
  );
}
