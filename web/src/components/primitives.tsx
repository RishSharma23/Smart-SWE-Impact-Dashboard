import * as React from 'react';

import { cn } from '@/lib/ui';

export function Card({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('card p-5', className)} {...props} />;
}

export function SectionHeading({
  id,
  eyebrow,
  title,
  description,
  level = 2,
  action,
}: {
  id?: string;
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  level?: 2 | 3;
  action?: React.ReactNode;
}) {
  const H = level === 2 ? 'h2' : 'h3';
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="max-w-2xl">
        {eyebrow ? (
          <p className="mb-1 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">{eyebrow}</p>
        ) : null}
        <H id={id} className={cn('font-semibold text-ink', level === 2 ? 'text-xl sm:text-2xl' : 'text-lg')}>
          {title}
        </H>
        {description ? <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

const badgeTones = {
  neutral: 'bg-surface-sunken text-ink-soft border-line-strong',
  accent: 'bg-accent-wash text-accent-ink border-accent',
  ok: 'bg-[#e6f2ea] text-ok border-[#9dc7ad]',
  warn: 'bg-[#fbf0d9] text-warn border-[#d8b263]',
  danger: 'bg-[#f8e6e6] text-danger border-[#d99f9f]',
  unknown: 'bg-surface-sunken text-unknown border-line-strong',
  outline: 'bg-surface text-ink-soft border-line-strong',
} as const;

export type BadgeTone = keyof typeof badgeTones;

export function Badge({
  tone = 'neutral',
  className,
  children,
  ...props
}: React.ComponentProps<'span'> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium leading-5',
        badgeTones[tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

export function Callout({
  tone = 'neutral',
  title,
  children,
  icon,
  className,
}: {
  tone?: 'neutral' | 'warn' | 'danger' | 'accent';
  title?: React.ReactNode;
  children?: React.ReactNode;
  icon?: React.ReactNode;
  className?: string;
}) {
  const tones = {
    neutral: 'border-line bg-surface',
    accent: 'border-accent bg-accent-wash',
    warn: 'border-[#d8b263] bg-[#fbf0d9]',
    danger: 'border-[#d99f9f] bg-[#f8e6e6]',
  } as const;
  return (
    <div className={cn('rounded-lg border p-4', tones[tone], className)}>
      <div className="flex gap-3">
        {icon ? <div className="mt-0.5 shrink-0">{icon}</div> : null}
        <div className="min-w-0 flex-1">
          {title ? <p className="mb-1 text-sm font-semibold text-ink">{title}</p> : null}
          <div className="text-sm leading-relaxed text-ink-soft">{children}</div>
        </div>
      </div>
    </div>
  );
}

/** A definition row. Used everywhere numbers and enums (chrome) are shown. */
export function KeyValue({
  label,
  children,
  hint,
  className,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
  className?: string;
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <dt className="font-mono text-[11px] uppercase tracking-[0.1em] text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-ink">{children}</dd>
      {hint ? <p className="mt-0.5 text-xs leading-snug text-muted">{hint}</p> : null}
    </div>
  );
}

/** Container that scrolls a wide table without ever scrolling the page. */
export function TableScroll({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div
      className="overflow-x-auto rounded-lg border border-line bg-surface"
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      {children}
    </div>
  );
}

export function DataTable({ className, ...props }: React.ComponentProps<'table'>) {
  return <table className={cn('w-full min-w-[34rem] border-collapse text-sm', className)} {...props} />;
}

export function Th({ className, ...props }: React.ComponentProps<'th'>) {
  return (
    <th
      scope={props.scope ?? 'col'}
      className={cn(
        'whitespace-nowrap border-b border-line bg-surface-sunken px-3 py-2 text-left font-mono text-[11px] uppercase tracking-[0.1em] text-muted',
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: React.ComponentProps<'td'>) {
  return <td className={cn('border-b border-line px-3 py-2 align-top text-ink-soft', className)} {...props} />;
}

/** "not observed in public data" — the only way this dashboard says "none". */
export function NotObserved({ reason, className }: { reason?: string | null; className?: string }) {
  return (
    <span className={cn('inline-flex flex-col gap-0.5', className)}>
      <span className="text-sm italic text-unknown">Not observed in public data</span>
      {reason ? <span className="text-xs leading-snug text-muted">{reason}</span> : null}
    </span>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong bg-surface/60 p-6 text-center">
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {children ? <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-muted">{children}</p> : null}
    </div>
  );
}
