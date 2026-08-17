import * as React from 'react';

import { intervalOf, type DimensionProfile } from '@/lib/schema';
import {
  BAND_NAMES,
  BAND_SCALE_MAX,
  bandName,
  cn,
  dimensionColor,
  dimensionLabel,
  humanize,
  sortDimensions,
  DIMENSION_SHORT,
} from '@/lib/ui';

import { EvidenceChip } from './EvidenceChip';
import { DataTable, TableScroll, Td, Th } from './primitives';

/**
 * Ordinal band bars — the primary, accessible dimension display.
 *
 * The one rule that matters here (contract §5.2): a null value is NOT a zero.
 * It renders as a full-width hatched slot labelled "not assessable" with the
 * reason attached, never as a bar of length zero.
 */
export function DimensionBandChart({
  profile,
  className,
  compact = false,
  highlight,
}: {
  profile: DimensionProfile[];
  className?: string;
  compact?: boolean;
  highlight?: string | null;
}) {
  const rows = sortDimensions(profile);
  if (rows.length === 0) return null;

  return (
    <div className={cn('space-y-3', className)}>
      {!compact ? <BandScaleLegend /> : null}
      <ul className="space-y-3">
        {rows.map((row) => (
          <li key={row.dimension}>
            <BandRow row={row} compact={compact} highlighted={highlight === row.dimension} />
          </li>
        ))}
      </ul>
      {!compact ? <DimensionTable profile={rows} /> : null}
    </div>
  );
}

function BandScaleLegend() {
  return (
    <div className="rounded-md border border-line bg-surface-sunken/60 px-2.5 py-1.5">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
        <span className="font-semibold text-ink-soft">band scale</span>
        {BAND_NAMES.map((name, i) => (
          <span key={name} className="whitespace-nowrap">
            <span className="text-ink-soft">{i}</span> {name}
          </span>
        ))}
      </p>
    </div>
  );
}

function BandRow({
  row,
  compact,
  highlighted,
}: {
  row: DimensionProfile;
  compact: boolean;
  highlighted?: boolean;
}) {
  const unknown = row.is_unknown || row.value === null || row.value === undefined;
  const color = dimensionColor(row.dimension);
  const pct = unknown ? 0 : Math.max(2, Math.min(100, ((row.value as number) / BAND_SCALE_MAX) * 100));
  const interval = intervalOf(row.interval);
  const label = dimensionLabel(row.dimension);

  return (
    <div className={cn('rounded-lg px-2 py-1.5 -mx-2', highlighted && 'bg-accent-wash')}>
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="flex items-center gap-2 text-sm font-medium text-ink">
          <span aria-hidden="true" className="size-2.5 shrink-0 rounded-[2px]" style={{ backgroundColor: color }} />
          {label}
        </span>
        <span className="flex items-center gap-2">
          {unknown ? (
            <span className="text-xs font-semibold uppercase tracking-wide text-unknown">Not assessable</span>
          ) : (
            <>
              <span className="text-xs font-semibold uppercase tracking-wide text-ink-soft">{bandName(row.value)}</span>
              <span className="font-mono text-[11px] text-muted">
                band {(row.value as number).toFixed(2)}
                <span className="sr-only"> out of {BAND_SCALE_MAX}</span>
              </span>
            </>
          )}
        </span>
      </div>

      <div
        role="img"
        aria-label={
          unknown
            ? `${label}: not assessable.${row.unknown_reason ? ` ${row.unknown_reason}` : ''}`
            : `${label}: band ${(row.value as number).toFixed(2)} of ${BAND_SCALE_MAX}, ${bandName(row.value)}` +
              (interval ? `, plausible range ${interval[0].toFixed(2)} to ${interval[1].toFixed(2)}` : '') +
              (row.confidence ? `, ${row.confidence} confidence` : '')
        }
        className="relative h-3 w-full overflow-hidden rounded-full border border-line bg-surface-sunken"
      >
        {unknown ? (
          <div className="hatch absolute inset-0 opacity-70" />
        ) : (
          <>
            {interval ? (
              <div
                className="absolute inset-y-0 rounded-full opacity-25"
                style={{
                  backgroundColor: color,
                  left: `${Math.max(0, (interval[0] / BAND_SCALE_MAX) * 100)}%`,
                  width: `${Math.max(1, ((interval[1] - interval[0]) / BAND_SCALE_MAX) * 100)}%`,
                }}
              />
            ) : null}
            <div className="absolute inset-y-0 left-0 rounded-full" style={{ backgroundColor: color, width: `${pct}%` }} />
          </>
        )}
        {/* band gridlines — 1, 2, 3 */}
        {[1, 2, 3].map((t) => (
          <span
            key={t}
            aria-hidden="true"
            className="absolute inset-y-0 w-px bg-ground/70"
            style={{ left: `${(t / BAND_SCALE_MAX) * 100}%` }}
          />
        ))}
      </div>

      {!compact ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <EvidenceChip grade={unknown ? 'unknown' : row.confidence === 'high' ? 'corroborated' : 'inferred'} />
          {row.confidence && !unknown ? <span>{humanize(row.confidence)} confidence</span> : null}
          {interval && !unknown ? (
            <span className="font-mono">
              range {interval[0].toFixed(1)}–{interval[1].toFixed(1)}
            </span>
          ) : null}
          {row.episode_count ? (
            <span>
              {row.episode_count} {row.episode_count === 1 ? 'episode' : 'episodes'}
            </span>
          ) : null}
          {unknown && row.unknown_reason ? <span className="italic">{row.unknown_reason}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Every visualization on this dashboard has a textual/tabular equivalent. */
export function DimensionTable({ profile }: { profile: DimensionProfile[] }) {
  return (
    <details className="group rounded-lg border border-line bg-surface">
      <summary className="cursor-pointer list-none px-3 py-2 text-xs font-medium text-muted transition-colors hover:text-ink [&::-webkit-details-marker]:hidden">
        <span className="inline-flex min-h-11 items-center gap-2">
          <span aria-hidden="true" className="transition-transform group-open:rotate-90">
            ▸
          </span>
          Same data as a table, with the aggregation trace
        </span>
      </summary>
      <div className="border-t border-line p-3">
        <TableScroll label="Dimension bands as a table">
          <DataTable>
            <caption className="sr-only">
              Six impact dimensions with band value, plausible range, confidence, episode count and the reason a
              dimension is not assessable.
            </caption>
            <thead>
              <tr>
                <Th>Dimension</Th>
                <Th>Band</Th>
                <Th>Value</Th>
                <Th>Range</Th>
                <Th>Confidence</Th>
                <Th>Episodes</Th>
                <Th>Why not assessable</Th>
              </tr>
            </thead>
            <tbody>
              {sortDimensions(profile).map((row) => {
                const unknown = row.is_unknown || row.value === null;
                return (
                  <tr key={row.dimension}>
                    <Th scope="row" className="bg-surface normal-case tracking-normal text-ink">
                      {dimensionLabel(row.dimension)}
                    </Th>
                    <Td>{unknown ? 'not assessable' : bandName(row.value)}</Td>
                    <Td className="font-mono">{unknown ? '—' : (row.value as number).toFixed(2)}</Td>
                    <Td className="font-mono">
                      {(() => {
                        const iv = intervalOf(row.interval);
                        return iv && !unknown ? `${iv[0].toFixed(2)}–${iv[1].toFixed(2)}` : '—';
                      })()}
                    </Td>
                    <Td>{row.confidence ? humanize(row.confidence) : '—'}</Td>
                    <Td className="font-mono">{row.episode_count ?? '—'}</Td>
                    <Td className="max-w-[18rem] text-xs">{row.unknown_reason ?? '—'}</Td>
                  </tr>
                );
              })}
            </tbody>
          </DataTable>
        </TableScroll>

        <AggregationTrace profile={profile} />
      </div>
    </details>
  );
}

function AggregationTrace({ profile }: { profile: DimensionProfile[] }) {
  const withTrace = profile.filter((p) => (p.aggregation_trace?.length ?? 0) > 0);
  if (withTrace.length === 0) return null;
  return (
    <div className="mt-4">
      <h4 className="mb-1 text-xs font-semibold text-ink">How each dimension value was aggregated</h4>
      <p className="mb-2 text-xs leading-relaxed text-muted">
        The strongest episode counts in full; each subsequent one contributes a decreasing share, capped by the remaining
        headroom. Ten mediocre episodes cannot out-weigh one strong one.
      </p>
      <TableScroll label="Portfolio aggregation trace">
        <DataTable>
          <thead>
            <tr>
              <Th>Dimension</Th>
              <Th>Rank</Th>
              <Th>Episode band</Th>
              <Th>Coefficient</Th>
              <Th>Contribution</Th>
              <Th>Headroom capped</Th>
            </tr>
          </thead>
          <tbody>
            {withTrace.flatMap((p) =>
              (p.aggregation_trace ?? []).map((t, i) => (
                <tr key={`${p.dimension}-${t.rank}`}>
                  <Td className="text-ink">{i === 0 ? dimensionLabel(p.dimension) : ''}</Td>
                  <Td className="font-mono">{t.rank}</Td>
                  <Td className="font-mono">{t.value.toFixed(2)}</Td>
                  <Td className="font-mono">{t.coefficient.toFixed(2)}</Td>
                  <Td className="font-mono">{t.contribution.toFixed(2)}</Td>
                  <Td>{t.headroom_capped ? 'yes' : 'no'}</Td>
                </tr>
              )),
            )}
          </tbody>
        </DataTable>
      </TableScroll>
    </div>
  );
}

// -- secondary radar ---------------------------------------------------------

/**
 * Secondary display only — the bars above are primary. Unknown spokes are drawn
 * as a grey hatched notch at the axis break, never pulled to the centre.
 */
export function DimensionRadar({
  profile,
  size = 240,
  className,
}: {
  profile: DimensionProfile[];
  size?: number;
  className?: string;
}) {
  const rows = sortDimensions(profile);
  if (rows.length < 3) return null;

  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 42;
  const n = rows.length;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const point = (i: number, frac: number) => [cx + Math.cos(angle(i)) * r * frac, cy + Math.sin(angle(i)) * r * frac];

  const known = rows.map((row) => !(row.is_unknown || row.value === null));
  const polygon = rows
    .map((row, i) => {
      // Unknown spokes are not plotted at 0; the polygon simply skips them.
      if (!known[i]) return null;
      const [x, y] = point(i, Math.max(0.04, (row.value as number) / BAND_SCALE_MAX));
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(' ');

  return (
    <figure className={cn('m-0', className)}>
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label={`Radar summary of six impact dimensions. ${rows
          .map((row) =>
            row.is_unknown || row.value === null
              ? `${dimensionLabel(row.dimension)}: not assessable`
              : `${dimensionLabel(row.dimension)}: band ${(row.value as number).toFixed(1)}`,
          )
          .join('. ')}.`}
        className="mx-auto block max-w-full"
      >
        {[1, 2, 3, 4].map((ring) => (
          <polygon
            key={ring}
            points={rows.map((_, i) => point(i, ring / 4).map((v) => v.toFixed(1)).join(',')).join(' ')}
            fill="none"
            stroke="var(--color-line)"
            strokeWidth="1"
          />
        ))}
        {rows.map((row, i) => {
          const [x, y] = point(i, 1);
          return (
            <line key={row.dimension} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--color-line)" strokeWidth="1" />
          );
        })}
        {polygon ? (
          <polygon points={polygon} fill="var(--color-accent)" fillOpacity="0.28" stroke="var(--color-accent-ink)" strokeWidth="1.5" />
        ) : null}
        {rows.map((row, i) => {
          const isKnown = known[i];
          const [x, y] = point(i, isKnown ? Math.max(0.04, (row.value as number) / BAND_SCALE_MAX) : 1.0);
                const [lx, ly] = point(i, 1.24);
          return (
            <g key={`pt-${row.dimension}`}>
              {isKnown ? (
                <circle cx={x} cy={y} r="3.5" fill={dimensionColor(row.dimension)} />
              ) : (
                <g>
                  <line
                    x1={x - 5}
                    y1={y - 5}
                    x2={x + 5}
                    y2={y + 5}
                    stroke="var(--color-unknown)"
                    strokeWidth="1.5"
                    strokeDasharray="2 2"
                  />
                  <line
                    x1={x + 5}
                    y1={y - 5}
                    x2={x - 5}
                    y2={y + 5}
                    stroke="var(--color-unknown)"
                    strokeWidth="1.5"
                    strokeDasharray="2 2"
                  />
                </g>
              )}
              <text
                x={lx}
                y={ly}
                textAnchor={lx > cx + 4 ? 'start' : lx < cx - 4 ? 'end' : 'middle'}
                dominantBaseline="middle"
                fontSize="8.5"
                fontFamily="var(--font-mono)"
                fill={isKnown ? 'var(--color-muted)' : 'var(--color-unknown)'}
              >
                {DIMENSION_SHORT[row.dimension] ?? dimensionLabel(row.dimension).split(' ')[0]}
                {isKnown ? '' : ' n/a'}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-center text-[11px] text-muted">
        Secondary view. Crossed spokes are <strong>not assessable</strong>, not zero.
      </figcaption>
    </figure>
  );
}
