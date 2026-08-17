import * as React from 'react';

import { cn, formatDate } from '@/lib/ui';

export interface TimelineStep {
  key: string;
  label: string;
  date?: string | null;
  detail?: React.ReactNode;
  tone?: 'default' | 'accent' | 'warn' | 'muted';
}

/**
 * Problem → intervention → observable result → propagation → durability.
 * Steps reveal in sequence on load; `prefers-reduced-motion` removes it and the
 * markup order is the reading order either way.
 */
export function Timeline({ steps, className }: { steps: TimelineStep[]; className?: string }) {
  return (
    <ol className={cn('relative space-y-5 border-l border-line pl-6', className)}>
      {steps.map((step, i) => (
        <li
          key={step.key}
          className="relative animate-fade-rise"
          style={{ animationDelay: `${Math.min(i * 70, 420)}ms` }}
        >
          <span
            aria-hidden="true"
            className={cn(
              'absolute -left-[1.9rem] top-1 size-3 rounded-full border-2 bg-ground',
              step.tone === 'accent'
                ? 'border-accent'
                : step.tone === 'warn'
                  ? 'border-warn'
                  : step.tone === 'muted'
                    ? 'border-line-strong'
                    : 'border-ink-soft',
            )}
          />
          <div className="flex flex-wrap items-baseline gap-x-3">
            <h3 className="text-sm font-semibold text-ink">{step.label}</h3>
            {step.date ? <span className="font-mono text-[11px] text-muted">{formatDate(step.date)}</span> : null}
          </div>
          {step.detail ? <div className="mt-1.5">{step.detail}</div> : null}
        </li>
      ))}
    </ol>
  );
}
