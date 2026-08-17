import * as React from 'react';

import { EVIDENCE_CHIP, cn, type EvidenceGrade } from '@/lib/ui';

const toneFor: Record<EvidenceGrade, string> = {
  direct: 'border-d2 bg-[#e8edfc] text-d2',
  corroborated: 'border-ok bg-[#e6f2ea] text-ok',
  inferred: 'border-warn bg-[#fbf0d9] text-warn',
  counterevidence: 'border-danger bg-[#f8e6e6] text-danger',
  unknown: 'border-line-strong bg-surface-sunken text-unknown',
};

/**
 * Shape carries the same information as colour: a filled square, a ring, a
 * dashed ring, a cross and a hatch. Nothing here is colour-only.
 */
function Glyph({ grade }: { grade: EvidenceGrade }) {
  const common = 'size-2.5 shrink-0';
  switch (grade) {
    case 'corroborated':
      return <span aria-hidden="true" className={cn(common, 'rounded-full bg-current')} />;
    case 'direct':
      return <span aria-hidden="true" className={cn(common, 'rounded-[2px] bg-current')} />;
    case 'inferred':
      return <span aria-hidden="true" className={cn(common, 'rounded-full border-[1.5px] border-dashed border-current')} />;
    case 'counterevidence':
      return (
        <svg aria-hidden="true" viewBox="0 0 10 10" className={cn(common, 'stroke-current')} strokeWidth="2">
          <path d="M1 1 L9 9 M9 1 L1 9" fill="none" strokeLinecap="round" />
        </svg>
      );
    default:
      return <span aria-hidden="true" className={cn(common, 'hatch rounded-[2px] border border-current')} />;
  }
}

export function EvidenceChip({
  grade,
  count,
  className,
  label,
}: {
  grade: EvidenceGrade;
  count?: number;
  className?: string;
  label?: string;
}) {
  const meta = EVIDENCE_CHIP[grade];
  const text = label ?? meta.label;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium',
        toneFor[grade],
        className,
      )}
      title={meta.description}
    >
      <Glyph grade={grade} />
      <span>{text}</span>
      {count !== undefined ? (
        <span className="font-mono text-[11px] opacity-80">
          <span className="sr-only">, </span>
          {count}
          <span className="sr-only"> {count === 1 ? 'source' : 'sources'}</span>
        </span>
      ) : null}
    </span>
  );
}

/** Legend for the chips. Rendered on the methodology page and the overview. */
export function EvidenceChipLegend({ className }: { className?: string }) {
  return (
    <dl className={cn('grid gap-3 sm:grid-cols-2', className)}>
      {(Object.keys(EVIDENCE_CHIP) as EvidenceGrade[]).map((grade) => (
        <div key={grade} className="flex gap-2.5">
          <dt className="shrink-0 pt-0.5">
            <EvidenceChip grade={grade} />
          </dt>
          <dd className="text-xs leading-relaxed text-muted">{EVIDENCE_CHIP[grade].description}</dd>
        </div>
      ))}
    </dl>
  );
}
