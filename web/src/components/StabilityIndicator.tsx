import * as React from 'react';

import { intervalOf, type Stability } from '@/lib/schema';
import { cn, formatPercent } from '@/lib/ui';

/**
 * Rank stability, shown as a range rather than a point. There is no composite
 * score here — the range and the inclusion frequency ARE the display.
 */
export function StabilityIndicator({
  stability,
  crossCheckDelta,
  className,
  compact = false,
}: {
  stability: Stability | null | undefined;
  crossCheckDelta?: number | null;
  className?: string;
  compact?: boolean;
}) {
  const range = intervalOf(stability?.position_range);
  const inclusion = stability?.top5_inclusion_probability ?? null;
  const rsi = stability?.rank_stability_index ?? null;

  if (!range && inclusion === null && rsi === null) {
    return (
      <p className={cn('text-xs italic text-unknown', className)}>
        Rank stability was not measured for this run.
      </p>
    );
  }

  const band = rsi === null ? 'unmeasured' : rsi >= 0.85 ? 'firm' : rsi >= 0.7 ? 'moderate' : 'sensitive';
  const bandCopy = {
    firm: 'Firm — the position holds across weight and resampling trials.',
    moderate: 'Moderate — the position moves under some plausible weightings.',
    sensitive: 'Sensitive — small weighting changes move this position.',
    unmeasured: 'Not measured for this run.',
  }[band];

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={cn(
            'inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide',
            band === 'firm'
              ? 'border-ok bg-[#e6f2ea] text-ok'
              : band === 'moderate'
                ? 'border-[#d8b263] bg-[#fbf0d9] text-warn'
                : band === 'sensitive'
                  ? 'border-danger bg-[#f8e6e6] text-danger'
                  : 'border-line-strong bg-surface-sunken text-unknown',
          )}
        >
          {band === 'unmeasured' ? 'stability unmeasured' : `${band} position`}
        </span>
        {range ? (
          <span className="font-mono text-xs text-ink-soft">
            positions {range[0]}–{range[1]}
            <span className="sr-only"> across resampling trials</span>
          </span>
        ) : null}
        {inclusion !== null ? (
          <span className="font-mono text-xs text-muted">
            in the top five {formatPercent(inclusion)} of trials
          </span>
        ) : null}
      </div>

      {range ? <RangeBar range={range} /> : null}

      {!compact ? <p className="text-xs leading-relaxed text-muted">{bandCopy}</p> : null}

      {crossCheckDelta ? (
        <p className="text-xs leading-relaxed text-warn">
          <strong>The two aggregation methods disagree here.</strong> PROMETHEE II places this engineer{' '}
          {Math.abs(crossCheckDelta)} {Math.abs(crossCheckDelta) === 1 ? 'position' : 'positions'}{' '}
          {crossCheckDelta > 0 ? 'lower' : 'higher'} than ELECTRE III. That is real information about how firm the
          position is, so it is shown rather than averaged away.
        </p>
      ) : null}
    </div>
  );
}

function RangeBar({ range }: { range: [number, number] }) {
  const max = Math.max(10, range[1]);
  const left = ((range[0] - 1) / max) * 100;
  const width = Math.max(4, ((range[1] - range[0] + 1) / max) * 100);
  return (
    <div
      role="img"
      aria-label={`Observed position range: ${range[0]} to ${range[1]}, on a scale from 1 to ${max}.`}
      className="relative h-2 w-full overflow-hidden rounded-full border border-line bg-surface-sunken"
    >
      <div className="absolute inset-y-0 rounded-full bg-ink-soft" style={{ left: `${left}%`, width: `${width}%` }} />
    </div>
  );
}
