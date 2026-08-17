'use client';

import { Menu, X } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { cn, formatDateTime, shortSha } from '@/lib/ui';

import { AnalysisWindowSelector, RankingScenarioSelector } from './Selectors';

export interface ShellMeta {
  title: string;
  subtitle: string | null;
  windowStart: string;
  windowEnd: string;
  lookbackDays: number | null;
  generatedAt: string;
  headSha: string;
  repositoryUrl: string;
  methodologyVersion: string;
}

const NAV = [
  { href: '/', label: 'Overview' },
  { href: '/engineers/', label: 'Engineers' },
  { href: '/compare/', label: 'Compare' },
  { href: '/methodology/', label: 'Methodology' },
  { href: '/coverage/', label: 'Coverage' },
];

export function AppShell({ meta, children }: { meta: ShellMeta; children: React.ReactNode }) {
  const pathname = usePathname() ?? '/';
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    setOpen(false);
  }, [pathname]);

  const isActive = (href: string) => (href === '/' ? pathname === '/' : pathname.startsWith(href));

  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[60] focus:rounded-md focus:border focus:border-ink focus:bg-surface focus:px-4 focus:py-2.5 focus:text-sm focus:font-medium focus:text-ink"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-30 border-b border-line bg-ground/92 backdrop-blur-sm">
        <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
          <div className="flex items-center justify-between gap-4 py-3">
            <Link href="/" className="group flex min-w-0 items-center gap-2.5">
              <Mark />
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold leading-tight text-ink sm:text-base">
                  PostHog Observable Engineering Impact
                </span>
                <span className="hidden truncate text-[11px] leading-tight text-muted sm:block">{meta.subtitle}</span>
              </span>
            </Link>

            <nav aria-label="Primary" className="hidden items-center gap-0.5 lg:flex">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={isActive(item.href) ? 'page' : undefined}
                  className={cn(
                    'inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium transition-colors',
                    isActive(item.href)
                      ? 'bg-surface text-ink shadow-[inset_0_0_0_1px_var(--color-line)]'
                      : 'text-ink-soft hover:bg-surface hover:text-ink',
                  )}
                >
                  {item.label}
                </Link>
              ))}
            </nav>

            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              aria-controls="mobile-nav"
              className="inline-flex size-11 items-center justify-center rounded-md border border-line-strong bg-surface text-ink-soft lg:hidden"
            >
              {open ? <X aria-hidden="true" className="size-5" /> : <Menu aria-hidden="true" className="size-5" />}
              <span className="sr-only">{open ? 'Close navigation' : 'Open navigation'}</span>
            </button>
          </div>

          {open ? (
            <nav id="mobile-nav" aria-label="Primary mobile" className="border-t border-line py-2 lg:hidden">
              <ul>
                {NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={isActive(item.href) ? 'page' : undefined}
                      className={cn(
                        'flex min-h-11 items-center rounded-md px-3 text-sm font-medium',
                        isActive(item.href) ? 'bg-surface text-ink' : 'text-ink-soft',
                      )}
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ) : null}

          {/* Controls + freshness. Kept in the header because the contract asks
              for them to be reachable from every route. */}
          <div className="flex flex-wrap items-start gap-x-6 gap-y-3 border-t border-line py-3">
            <RankingScenarioSelector />
            <AnalysisWindowSelector
              start={meta.windowStart}
              end={meta.windowEnd}
              lookbackDays={meta.lookbackDays}
            />
            <div className="min-w-0">
              <p className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Data freshness</p>
              <p className="font-mono text-[11px] leading-5 text-ink-soft">
                generated {formatDateTime(meta.generatedAt)}
                <span className="mx-1.5 text-line-strong">·</span>
                <a
                  href={`${meta.repositoryUrl}/commit/${meta.headSha}`}
                  target="_blank"
                  rel="noopener noreferrer external"
                  className="underline decoration-line-strong underline-offset-2 hover:text-ink"
                >
                  {shortSha(meta.headSha)}
                </a>
                <span className="mx-1.5 text-line-strong">·</span>
                methodology {meta.methodologyVersion}
              </p>
            </div>
          </div>
        </div>

        {/* The compact standing notice. Not a dismissible toast — it is a
            qualifier on everything below it. */}
        <p className="border-t border-accent/40 bg-accent-wash px-4 py-1.5 text-center text-[11px] leading-relaxed text-accent-ink sm:px-6">
          Public-repository impact is <strong>not</strong> total employee productivity. No evidence here is not negative
          evidence.
        </p>
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-10">
        {children}
      </main>
    </div>
  );
}

function Mark() {
  return (
    <span
      aria-hidden="true"
      className="grid size-8 shrink-0 place-items-center rounded-md border border-line-strong bg-surface"
    >
      <svg viewBox="0 0 16 16" className="size-4">
        <rect x="1" y="9" width="3" height="6" fill="var(--color-d1)" />
        <rect x="5.5" y="5" width="3" height="10" fill="var(--color-d3)" />
        <rect x="10" y="1" width="3" height="14" fill="var(--color-accent)" />
      </svg>
    </span>
  );
}
