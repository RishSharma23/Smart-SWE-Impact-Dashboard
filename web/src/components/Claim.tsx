'use client';

import * as Dialog from '@radix-ui/react-dialog';
import { Check, Copy, FileSearch, X } from 'lucide-react';
import * as React from 'react';

import type { Claim as ClaimType } from '@/lib/schema';
import { cn, humanize } from '@/lib/ui';

import { EvidenceChip } from './EvidenceChip';
import { SourceLink } from './SourceLink';
import { Badge } from './primitives';

/**
 * Contract rule 3: every human-readable sentence is a claim_id lookup, and every
 * rendered claim exposes its evidence and its claim_id. A null claim renders
 * NOTHING — not a placeholder, not the raw field.
 */
export function Claim({
  claim,
  as: As = 'p',
  className,
  tone = 'body',
}: {
  claim: ClaimType | null | undefined;
  as?: 'p' | 'span' | 'h3' | 'div';
  className?: string;
  tone?: 'body' | 'lead' | 'inline';
}) {
  if (!claim) return null;
  const toneClass =
    tone === 'lead'
      ? 'text-base leading-relaxed text-ink sm:text-[17px]'
      : tone === 'inline'
        ? 'text-sm leading-relaxed text-ink-soft'
        : 'text-sm leading-relaxed text-ink-soft';
  return (
    <As className={cn(toneClass, className)}>
      {claim.text}{' '}
      <EvidenceTrigger claim={claim} />
    </As>
  );
}

/** Several claims in sequence — a thesis is usually 2-4 of them. */
export function ClaimList({
  claims,
  className,
  tone = 'body',
}: {
  claims: (ClaimType | null | undefined)[];
  className?: string;
  tone?: 'body' | 'lead';
}) {
  const present = claims.filter(Boolean) as ClaimType[];
  if (present.length === 0) return null;
  return (
    <div className={cn('prose-editorial', className)}>
      {present.map((c) => (
        <Claim key={c.claim_id} claim={c} tone={tone} />
      ))}
    </div>
  );
}

// -- the drawer --------------------------------------------------------------

export function EvidenceTrigger({ claim, label }: { claim: ClaimType; label?: string }) {
  const n = claim.evidence?.length ?? claim.evidence_count ?? 0;
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="inline-flex translate-y-px items-center gap-1 rounded border border-line-strong bg-surface-sunken px-1.5 py-0.5 align-baseline font-mono text-[10px] font-semibold uppercase tracking-wider text-muted transition-colors hover:border-accent hover:bg-accent-wash hover:text-accent-ink"
        >
          <FileSearch aria-hidden="true" className="size-3" />
          {label ?? 'Evidence'}
          {n > 0 ? <span aria-hidden="true">·{n}</span> : null}
          <span className="sr-only">
            {' '}
            for the claim &ldquo;{claim.text.slice(0, 80)}
            {claim.text.length > 80 ? '…' : ''}&rdquo; — {n} {n === 1 ? 'source' : 'sources'}
          </span>
        </button>
      </Dialog.Trigger>
      <EvidenceDrawer claim={claim} />
    </Dialog.Root>
  );
}

function EvidenceDrawer({ claim }: { claim: ClaimType }) {
  const evidence = claim.evidence ?? [];
  return (
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/35 backdrop-blur-[1px] data-[state=open]:animate-fade-rise" />
      <Dialog.Content
        className="fixed inset-x-0 bottom-0 z-50 max-h-[85vh] overflow-y-auto rounded-t-2xl border border-line bg-surface p-5 shadow-2xl focus:outline-none sm:inset-y-0 sm:left-auto sm:right-0 sm:h-full sm:max-h-none sm:w-[min(30rem,100vw)] sm:rounded-l-2xl sm:rounded-tr-none sm:p-6"
        aria-describedby={undefined}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <Dialog.Title className="text-sm font-semibold uppercase tracking-[0.1em] text-muted">
            Evidence for this claim
          </Dialog.Title>
          <Dialog.Close asChild>
            <button
              type="button"
              className="-m-2 inline-flex size-11 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-sunken hover:text-ink"
            >
              <X aria-hidden="true" className="size-5" />
              <span className="sr-only">Close evidence panel</span>
            </button>
          </Dialog.Close>
        </div>

        <blockquote className="mb-4 border-l-2 border-accent pl-3 text-base leading-relaxed text-ink">
          {claim.text}
        </blockquote>

        <dl className="mb-5 grid grid-cols-2 gap-3 border-y border-line py-3">
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Claim type</dt>
            <dd className="mt-1 text-sm text-ink">{humanize(claim.claim_type)}</dd>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Confidence</dt>
            <dd className="mt-1 text-sm text-ink">
              {claim.confidence ? (
                <Badge tone={claim.confidence === 'high' ? 'ok' : claim.confidence === 'low' ? 'warn' : 'neutral'}>
                  {humanize(claim.confidence)}
                </Badge>
              ) : (
                <span className="italic text-unknown">not stated</span>
              )}
            </dd>
          </div>
          {claim.derivation ? (
            <div className="col-span-2">
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Derived by</dt>
              <dd className="mt-1 break-words font-mono text-xs text-ink-soft">{claim.derivation}</dd>
            </div>
          ) : null}
        </dl>

        <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink">
          {claim.evidence_is_methodological ? 'Methodological basis' : 'Source artifacts'}
          <EvidenceChip grade={claim.evidence_is_methodological ? 'inferred' : 'direct'} count={evidence.length} />
        </h3>

        {claim.evidence_is_methodological ? (
          <p className="mb-4 text-xs leading-relaxed text-muted">
            This claim is about the method itself, so its basis is the methodology rather than a repository artifact.
          </p>
        ) : null}

        {evidence.length === 0 ? (
          <p className="text-sm italic text-unknown">
            No artifact was attached to this claim. It is rendered because Phase 2 published it, but it cannot be traced
            further from here.
          </p>
        ) : (
          <ul className="space-y-2.5">
            {evidence.map((e, i) => (
              <li key={`${e.artifact_id ?? e.url ?? i}`} className="rounded-lg border border-line bg-ground/60 p-3">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <Badge tone="outline">{humanize(e.kind) || 'artifact'}</Badge>
                  {e.role ? <Badge tone="accent">{humanize(e.role)}</Badge> : null}
                </div>
                {e.detail ? <p className="mb-1.5 text-xs leading-relaxed text-ink-soft">{e.detail}</p> : null}
                {e.url ? (
                  <SourceLink href={e.url} className="text-xs">
                    {e.url.replace('https://github.com/', '')}
                  </SourceLink>
                ) : e.artifact_id ? (
                  <p className="break-all font-mono text-[11px] text-muted">{e.artifact_id}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        <ClaimIdBlock claimId={claim.claim_id} />
      </Dialog.Content>
    </Dialog.Portal>
  );
}

/** The correction pathway is "quote the claim_id", so it has to be copyable. */
export function ClaimIdBlock({ claimId }: { claimId: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="mt-6 border-t border-line pt-4">
      <p className="mb-2 text-xs leading-relaxed text-muted">
        Think this is wrong? Open an issue quoting the claim id below — every claim on this dashboard is addressable.
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded border border-line bg-surface-sunken px-2 py-1.5 font-mono text-[11px] text-ink-soft">
          {claimId}
        </code>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(claimId).then(
              () => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1800);
              },
              () => undefined,
            );
          }}
          className="inline-flex h-11 min-w-11 items-center justify-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-xs font-medium text-ink-soft transition-colors hover:border-accent hover:bg-accent-wash"
        >
          {copied ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
          {copied ? 'Copied' : 'Copy'}
          <span className="sr-only"> claim id</span>
        </button>
      </div>
      <p aria-live="polite" className="sr-only">
        {copied ? 'Claim id copied to clipboard' : ''}
      </p>
    </div>
  );
}
