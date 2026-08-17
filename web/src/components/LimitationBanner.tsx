import { AlertTriangle, FlaskConical, ShieldAlert } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { cn } from '@/lib/ui';

/**
 * The limitations headline. Contract §9: required on the landing page, not
 * buried in a footer.
 */
export function LimitationBanner({ headline, className }: { headline: string; className?: string }) {
  return (
    <aside
      aria-labelledby="limitations-headline"
      className={cn('rounded-lg border border-line bg-surface p-4 sm:p-5', className)}
    >
      <div className="flex gap-3">
        <ShieldAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-warn" />
        <div>
          <p id="limitations-headline" className="text-sm font-semibold leading-relaxed text-ink sm:text-base">
            {headline}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted">
            <Link href="/methodology/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
              How impact is defined and measured
            </Link>
            <span className="mx-2 text-line-strong">·</span>
            <Link href="/coverage/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
              What this run could not see
            </Link>
          </p>
        </div>
      </div>
    </aside>
  );
}

/**
 * Contract §2.1: `publishable: false` requires a persistent banner naming the
 * outstanding human-review blockers.
 */
export function ProvisionalBanner({
  blockers,
  validationStatus,
  approval,
  className,
}: {
  blockers: { item: string; status?: string | null; queue_file?: string | null }[];
  validationStatus: string;
  /** A human sign-off recorded at build time, if one exists. */
  approval?: string | null;
  className?: string;
}) {
  // A human has reviewed and approved publication. Phase 2's automated verdict
  // is still reported verbatim — the reader gets both, not a rewritten one.
  if (approval) return <ApprovedBanner blockers={blockers} validationStatus={validationStatus} approval={approval} className={className} />;

  return (
    <div
      role="note"
      aria-label="Provisional run notice"
      className={cn('rounded-lg border-2 border-warn bg-[#fbf0d9] p-4 sm:p-5', className)}
    >
      <div className="flex gap-3">
        <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-warn" />
        <div className="min-w-0">
          <p className="text-sm font-bold uppercase tracking-wide text-warn">Provisional — not yet human reviewed</p>
          <p className="mt-1 text-sm leading-relaxed text-ink">
            This run is published with <code className="font-mono text-xs">publishable: false</code> and validation
            status <code className="font-mono text-xs">{validationStatus}</code>. The deterministic pipeline completed,
            but the human sign-off items below are still outstanding. Read the rankings as a draft.
          </p>
          {blockers.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {blockers.map((b) => (
                <li
                  key={b.item}
                  className="inline-flex items-center gap-1.5 rounded border border-warn/60 bg-surface px-2 py-0.5 font-mono text-[11px] text-warn"
                  title={b.queue_file ? `queue: ${b.queue_file}` : undefined}
                >
                  {b.item}
                  {b.status ? <span className="opacity-70">· {b.status}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ApprovedBanner({
  blockers,
  validationStatus,
  approval,
  className,
}: {
  blockers: { item: string; status?: string | null; queue_file?: string | null }[];
  validationStatus: string;
  approval: string;
  className?: string;
}) {
  return (
    <div
      role="note"
      aria-label="Review status"
      className={cn('rounded-lg border border-line bg-surface p-4 sm:p-5', className)}
    >
      <div className="flex gap-3">
        <ShieldAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-ok" />
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">Reviewed and approved for publication</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">{approval}</p>
          <details className="mt-2">
            <summary className="inline-flex min-h-9 cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
              <span aria-hidden="true">▸</span>
              What Phase 2&apos;s automated gate still reports
            </summary>
            <div className="mt-2 border-l-2 border-line pl-3">
              <p className="text-xs leading-relaxed text-muted">
                The export was written with <code className="font-mono">publishable: false</code> and validation status{' '}
                <code className="font-mono">{validationStatus}</code>. That verdict is Phase 2&apos;s and is reported
                here unchanged; the approval above is a separate human record, not a rewrite of it. The queue items
                below are the ones the automated gate was waiting on.
              </p>
              {blockers.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-1.5">
                  {blockers.map((b) => (
                    <li
                      key={b.item}
                      className="inline-flex items-center gap-1.5 rounded border border-line-strong bg-surface-sunken px-2 py-0.5 font-mono text-[11px] text-muted"
                      title={b.queue_file ? `queue: ${b.queue_file}` : undefined}
                    >
                      {b.item}
                      {b.status ? <span className="opacity-70">· {b.status}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

/** Development fixture mode. Impossible to miss, impossible to reach in prod. */
export function DemoDataBanner({ note, className }: { note?: string | null; className?: string }) {
  return (
    <div
      role="note"
      aria-label="Demo data notice"
      className={cn('rounded-lg border-2 border-dashed border-d3 bg-[#f3ebfb] p-4', className)}
    >
      <div className="flex gap-3">
        <FlaskConical aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-d3" />
        <div>
          <p className="font-mono text-sm font-bold uppercase tracking-[0.15em] text-d3">Demo data</p>
          <p className="mt-1 text-sm leading-relaxed text-ink">
            {note ??
              'Every name, episode and number on this page is synthetic fixture data used to develop the UI. It describes no real person.'}
          </p>
        </div>
      </div>
    </div>
  );
}
