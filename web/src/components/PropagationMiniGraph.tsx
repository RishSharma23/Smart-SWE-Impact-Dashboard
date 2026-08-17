import * as React from 'react';

import type { Propagation } from '@/lib/schema';
import { cn, formatNumber, REACHABILITY_LABEL } from '@/lib/ui';

import { DataTable, TableScroll, Td, Th } from './primitives';

/**
 * Propagation and durability, shown as a small deterministic layout rather than
 * a force simulation — the numbers are the point, and every one of them has an
 * accessible table below it.
 *
 * The measurements are: how far the change reached, how many downstream authors
 * built on it, and how much of that reach survives time decay.
 */
export function PropagationMiniGraph({
  propagation,
  reachabilityBand,
  className,
}: {
  propagation: Propagation | null | undefined;
  reachabilityBand?: string | null;
  className?: string;
}) {
  if (!propagation) {
    return (
      <p className={cn('text-sm italic text-unknown', className)}>
        Propagation was not measured for this episode.
      </p>
    );
  }

  const files = propagation.reach_file_count ?? 0;
  const prs = propagation.reach_pr_count ?? 0;
  const authors = propagation.distinct_downstream_authors ?? 0;
  const depth = propagation.max_path_depth ?? 0;
  const components = propagation.components_reached ?? [];
  const decay = propagation.effective_decay_factor;

  return (
    <div className={cn('space-y-4', className)}>
      <Diagram files={files} prs={prs} authors={authors} depth={depth} components={components.length} />

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Files reached" value={formatNumber(propagation.reach_file_count)} />
        <Stat label="Downstream PRs" value={formatNumber(propagation.reach_pr_count)} />
        <Stat label="Other authors built on it" value={formatNumber(propagation.distinct_downstream_authors)} />
        <Stat label="Max path depth" value={formatNumber(propagation.max_path_depth)} />
      </dl>

      {components.length > 0 ? (
        <div>
          <p className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Components reached</p>
          <ul className="flex flex-wrap gap-1.5">
            {components.map((c) => (
              <li
                key={c}
                className="rounded border border-line-strong bg-surface px-2 py-0.5 font-mono text-[11px] text-ink-soft"
              >
                {c}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <TableScroll label="Propagation and durability measurements">
        <DataTable>
          <caption className="sr-only">
            Every propagation and durability measurement for this episode, including the caps that were applied.
          </caption>
          <thead>
            <tr>
              <Th>Measurement</Th>
              <Th>Value</Th>
              <Th>What it means</Th>
            </tr>
          </thead>
          <tbody>
            <Row label="Reachability band" value={reachabilityBand ? (REACHABILITY_LABEL[reachabilityBand] ?? reachabilityBand) : '—'}>
              How far the change is reachable through the import graph. &ldquo;Not assessable&rdquo; means the language
              has no import parser, not that reach was small.
            </Row>
            <Row label="Distinct component penetration" value={formatNumber(propagation.distinct_component_penetration)}>
              How many separate components the change reached.
            </Row>
            <Row label="Downstream authors" value={formatNumber(propagation.distinct_downstream_authors)}>
              People other than the author who subsequently built on this code. Hub files are damped so a popular
              utility does not inflate this.
            </Row>
            <Row label="Source age" value={propagation.source_age_days ? `${propagation.source_age_days.toFixed(1)} days` : '—'}>
              How long the work has had to propagate.
            </Row>
            <Row label="Raw decay factor" value={propagation.raw_decay_factor?.toFixed(3) ?? '—'}>
              Time decay applied to reach, on an exponential half-life.
            </Row>
            <Row label="Persistence detected" value={propagation.persistence_detected ? 'yes' : 'no'}>
              Whether the change is still being built on rather than merely surviving.
            </Row>
            <Row label="Effective decay factor" value={decay?.toFixed(3) ?? '—'}>
              Decay after persistence is taken into account. Work that keeps attracting downstream change decays less.
            </Row>
            <Row label="Mass after cap" value={propagation.mass_after_cap?.toFixed(2) ?? '—'}>
              Total propagation mass once hub damping and the per-episode edge cap are applied.
            </Row>
            <Row label="Cap applied" value={propagation.cap_applied ? 'yes' : 'no'}>
              The walk stops at a fixed edge budget for tractability. When it does, the episode says so rather than
              silently reporting a smaller number.
            </Row>
            {propagation.walk_truncated !== null && propagation.walk_truncated !== undefined ? (
              <Row label="Walk truncated" value={propagation.walk_truncated ? 'yes' : 'no'}>
                The traversal hit the per-episode edge limit. Reach is a lower bound here.
              </Row>
            ) : null}
            {propagation.reason ? (
              <Row label="Reason" value={propagation.reason}>
                Why propagation could not be measured further.
              </Row>
            ) : null}
          </tbody>
        </DataTable>
      </TableScroll>
    </div>
  );
}

function Row({ label, value, children }: { label: string; value: React.ReactNode; children: React.ReactNode }) {
  return (
    <tr>
      <Th scope="row" className="bg-surface normal-case tracking-normal text-ink">
        {label}
      </Th>
      <Td className="whitespace-nowrap font-mono text-ink">{value}</Td>
      <Td className="max-w-[26rem] text-xs">{children}</Td>
    </tr>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 font-mono text-lg font-semibold text-ink">{value}</dd>
    </div>
  );
}

/** A three-ring reach diagram. Decorative; every number is in the table above. */
function Diagram({
  files,
  prs,
  authors,
  depth,
  components,
}: {
  files: number;
  prs: number;
  authors: number;
  depth: number;
  components: number;
}) {
  const scale = (n: number, max: number) => Math.min(1, Math.log10(1 + n) / Math.log10(1 + max));
  const rings = [
    { label: 'files', n: files, r: 26 + scale(files, 500) * 44, color: 'var(--color-d3)' },
    { label: 'PRs', n: prs, r: 22 + scale(prs, 120) * 32, color: 'var(--color-d2)' },
    { label: 'authors', n: authors, r: 16 + scale(authors, 40) * 20, color: 'var(--color-d1)' },
  ];

  return (
    <figure className="m-0">
      <svg
        viewBox="0 0 240 160"
        className="h-auto w-full max-w-sm"
        role="img"
        aria-label={`Reach diagram: ${files} files, ${prs} downstream pull requests, ${authors} downstream authors, across ${components} components, at a maximum graph depth of ${depth}. The same figures appear in the table below.`}
      >
        <g transform="translate(80,80)">
          {rings.map((ring) => (
            <circle
              key={ring.label}
              r={ring.r}
              fill={ring.color}
              fillOpacity="0.1"
              stroke={ring.color}
              strokeWidth="1.25"
              strokeDasharray={ring.n === 0 ? '3 3' : undefined}
            />
          ))}
          <circle r="5" fill="var(--color-accent)" stroke="var(--color-accent-ink)" strokeWidth="1" />
        </g>
        <g fontFamily="var(--font-mono)" fontSize="9" fill="var(--color-muted)">
          {rings.map((ring, i) => (
            <g key={ring.label}>
              <line
                x1={80 + ring.r}
                y1={80}
                x2={162}
                y2={34 + i * 22}
                stroke="var(--color-line-strong)"
                strokeWidth="0.75"
              />
              <text x={166} y={37 + i * 22} fill="var(--color-ink-soft)">
                {ring.n.toLocaleString('en-GB')} {ring.label}
              </text>
            </g>
          ))}
          <text x={166} y={103} fill="var(--color-muted)">
            {components} components
          </text>
          <text x={166} y={119} fill="var(--color-muted)">
            depth {depth}
          </text>
          <text x={80} y={150} textAnchor="middle" fill="var(--color-muted)">
            this episode
          </text>
        </g>
      </svg>
    </figure>
  );
}
