import * as React from 'react';

import { formatDate, formatDateTime, formatNumber } from '@/lib/ui';

import { SourceLink } from './SourceLink';

export interface BuildMeta {
  generatedAt: string;
  stagedAt: string;
  headSha: string;
  repositoryUrl: string;
  isShallowClone: boolean;
  windowStart: string;
  windowEnd: string;
  manifestVersion: string;
  methodologyVersion: string;
  sourceDir: string;
  isFixture: boolean;
  fileCount: number;
  totalBytes: number;
  /** `projection` or `full`. Absent on a package written before either existed. */
  exportMode: string | null;
  counts: Record<string, number>;
  validationStatus: string;
  publishable: boolean;
  safetyScan: string | null;
  correctionInstructions: string | null;
}

export function BuildMetadataFooter({ meta }: { meta: BuildMeta }) {
  return (
    <footer className="mt-16 border-t border-line bg-surface/60">
      <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
        <h2 className="mb-4 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">Build and data provenance</h2>

        <dl className="grid gap-x-6 gap-y-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <Item label="Source repository">
            <SourceLink href={meta.repositoryUrl}>{meta.repositoryUrl.replace('https://', '')}</SourceLink>
          </Item>
          <Item label="Analysed commit" hint={meta.isShallowClone ? 'shallow clone — history looks forward only' : undefined}>
            <SourceLink href={`${meta.repositoryUrl}/commit/${meta.headSha}`}>
              <span className="font-mono">{meta.headSha.slice(0, 12)}</span>
            </SourceLink>
          </Item>
          <Item label="Analysis window">
            <span className="font-mono">
              {formatDate(meta.windowStart)} → {formatDate(meta.windowEnd)}
            </span>
          </Item>
          <Item label="Generated">
            <span className="font-mono">{formatDateTime(meta.generatedAt)}</span>
          </Item>

          <Item label="Export schema">
            <span className="font-mono">{meta.manifestVersion}</span>
          </Item>
          <Item label="Methodology">
            <span className="font-mono">{meta.methodologyVersion}</span>
          </Item>
          <Item
            label="Package"
            hint={
              meta.exportMode === 'projection'
                ? 'a projection: the records these pages resolve, each one whole. See the coverage page.'
                : undefined
            }
          >
            <span className="font-mono">
              {meta.sourceDir} · {meta.fileCount} files · {(meta.totalBytes / 1024 / 1024).toFixed(2)} MB
              {meta.exportMode ? ` · ${meta.exportMode}` : ''}
            </span>
          </Item>
          <Item label="Staged at build">
            <span className="font-mono">{formatDateTime(meta.stagedAt)}</span>
          </Item>

          <Item label="Validation">
            <span className="font-mono">{meta.validationStatus}</span>
          </Item>
          <Item label="Publishable">
            <span className="font-mono">{String(meta.publishable)}</span>
          </Item>
          <Item label="Safety scan">
            <span className="font-mono">{meta.safetyScan ?? '—'}</span>
          </Item>
          <Item label="Data mode">
            <span className="font-mono">{meta.isFixture ? 'SYNTHETIC FIXTURE' : 'real export'}</span>
          </Item>
        </dl>

        <div className="mt-6 border-t border-line pt-4">
          <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted">
            {Object.entries(meta.counts).map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-1.5">
                <dt className="font-mono uppercase tracking-wide">{k.replace(/_/g, ' ')}</dt>
                <dd className="font-mono font-semibold text-ink-soft">{formatNumber(v)}</dd>
              </div>
            ))}
          </dl>
        </div>

        {meta.correctionInstructions ? (
          <p className="mt-6 max-w-3xl text-xs leading-relaxed text-muted">
            <strong className="text-ink-soft">Corrections.</strong> {meta.correctionInstructions}
          </p>
        ) : null}

        <p className="mt-4 max-w-3xl text-xs leading-relaxed text-muted">
          Counts are coverage statistics describing what was analysed. They are not productivity statistics and are not
          used in any ranking.
        </p>
      </div>
    </footer>
  );
}

function Item({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 break-words text-ink-soft">{children}</dd>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}
