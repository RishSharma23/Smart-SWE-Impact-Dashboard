import type { Metadata } from 'next';
import * as React from 'react';

import { JsonSpec } from '@/components/JsonSpec';
import { ProvisionalBanner } from '@/components/LimitationBanner';
import {
  Badge,
  Callout,
  Card,
  DataTable,
  EmptyState,
  KeyValue,
  SectionHeading,
  TableScroll,
  Td,
  Th,
} from '@/components/primitives';
import { loadBundle } from '@/lib/data';
import { dimensionLabel, formatDateTime, formatNumber, formatPercent, humanize, titleize } from '@/lib/ui';

export const metadata: Metadata = {
  title: 'Coverage',
  description:
    'What this run could and could not see: input verification, missingness per dimension, known gaps, disabled capabilities and validation status.',
};

const SEVERITY_TONE: Record<string, 'danger' | 'warn' | 'neutral'> = {
  structural: 'warn',
  blocking: 'danger',
  minor: 'neutral',
};

export default function CoveragePage() {
  const bundle = loadBundle();
  const { coverage, manifest } = bundle;
  const missing = coverage.missingness;
  const disabled = Object.entries(coverage.capabilities_disabled ?? {});
  const validation = coverage.validation;
  // Absent on a package written before the export could project; those were
  // always complete, so there is nothing to declare.
  const pkg = coverage.package ?? manifest.projection ?? null;

  return (
    <div className="space-y-10">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold leading-tight text-ink">What this run could not see</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">
          Coverage is the honest half of the analysis. Every gap below changes how a result should be read, so none of
          them is buried. An unknown value is never rendered as a zero anywhere in this dashboard.
        </p>
      </header>

      {!manifest.publishable ? (
        <ProvisionalBanner
          blockers={manifest.publishable_blockers}
          validationStatus={manifest.validation_status}
          approval={bundle.provenance.publish_approval ?? null}
        />
      ) : null}

      {/* Non-fatal defects found while loading the package. */}
      {bundle.dataWarnings.length > 0 ? (
        <section aria-labelledby="defects-heading">
          <SectionHeading
            id="defects-heading"
            title="Defects found in this export"
            description="Problems the dashboard detected in the published package itself. They are shown rather than worked around, and each one is queued to go back to Phase 2."
          />
          <ul className="space-y-2">
            {bundle.dataWarnings.map((w) => (
              <li key={w}>
                <Callout tone="warn">
                  <p>{w}</p>
                </Callout>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* What this package carries, against what the run analysed. */}
      {pkg ? (
        <section aria-labelledby="package-heading">
          <SectionHeading
            id="package-heading"
            title="What is in this package"
            description="The analysis produces more than the site renders. By default the published package carries the records a page or a listing actually resolves, each one exactly as the pipeline produced it. Nothing here is a rounded or aggregated stand-in for a record that was left out."
          />
          <Card>
            <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
              <KeyValue label="export mode">
                <span className="font-mono">{pkg.export_mode ?? manifest.export_mode ?? 'full'}</span>
              </KeyValue>
              <KeyValue label="episodes">
                <span className="font-mono">
                  {formatNumber(pkg.episodes_included)} of{' '}
                  {formatNumber((pkg.episodes_included ?? 0) + (pkg.episodes_omitted ?? 0))}
                </span>
              </KeyValue>
              <KeyValue label="claims">
                <span className="font-mono">
                  {formatNumber(pkg.claims_included)} of{' '}
                  {formatNumber((pkg.claims_included ?? 0) + (pkg.claims_omitted ?? 0))}
                </span>
              </KeyValue>
              <KeyValue label="evidence artifacts">
                <span className="font-mono">
                  {formatNumber(pkg.evidence_artifacts_included)} of{' '}
                  {formatNumber(
                    (pkg.evidence_artifacts_included ?? 0) + (pkg.evidence_artifacts_omitted ?? 0),
                  )}
                </span>
              </KeyValue>
              <KeyValue label="episode pages">
                <span className="font-mono">{formatNumber(pkg.episode_pages)}</span>
              </KeyValue>
              {(pkg.episode_pages_truncated ?? 0) > 0 ? (
                <KeyValue label="pages past the cap">
                  <span className="font-mono">{formatNumber(pkg.episode_pages_truncated)}</span>
                </KeyValue>
              ) : null}
            </dl>
            {pkg.rule ? (
              <p className="mt-4 text-xs leading-relaxed text-muted">
                <span className="font-medium text-ink-soft">The rule: </span>
                {pkg.rule}
              </p>
            ) : null}
            {pkg.full_package ? <p className="mt-2 text-xs leading-relaxed text-muted">{pkg.full_package}</p> : null}
          </Card>
        </section>
      ) : null}

      {/* Input verification. */}
      <section aria-labelledby="phase1-heading">
        <SectionHeading
          id="phase1-heading"
          title="Input verification"
          description="Every Phase 1 table is content-hashed before Phase 2 reads it. A hash mismatch fails the run rather than producing a quietly wrong answer."
        />
        <Card>
          <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
            {Object.entries(coverage.phase1 ?? {}).map(([k, v]) => (
              <KeyValue key={k} label={k.replace(/_/g, ' ')}>
                <span className="font-mono">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
              </KeyValue>
            ))}
            <KeyValue label="generated at">
              <span className="font-mono">{formatDateTime(manifest.generated_at)}</span>
            </KeyValue>
            <KeyValue label="safety scan">
              <span className="font-mono">{manifest.safety_scan?.status ?? '—'}</span>
            </KeyValue>
          </dl>
        </Card>
      </section>

      {/* Missingness. */}
      <section aria-labelledby="missingness-heading">
        <SectionHeading
          id="missingness-heading"
          title="How much is unknown, per dimension"
          description="The share of episodes where a dimension could not be assessed at all. A high rate does not mean low impact on that dimension — it means the public record could not speak to it."
        />
        {Object.keys(missing?.dimension_unknown_rates ?? {}).length === 0 ? (
          <EmptyState title="No per-dimension missingness was published for this run." />
        ) : (
          <Card>
            <ul className="space-y-3">
              {Object.entries(missing!.dimension_unknown_rates).map(([dim, stats]) => {
                const rate = stats.unknown_rate ?? 0;
                return (
                  <li key={dim}>
                    <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
                      <span className="text-sm font-medium text-ink">{dimensionLabel(dim)}</span>
                      <span className="font-mono text-xs text-muted">
                        {formatNumber(stats.unknown)} unknown of {formatNumber((stats.assessed ?? 0) + (stats.unknown ?? 0))}{' '}
                        · {formatPercent(rate, 1)}
                      </span>
                    </div>
                    <div
                      role="img"
                      aria-label={`${dimensionLabel(dim)}: ${formatPercent(rate, 1)} of episodes could not be assessed.`}
                      className="relative h-2.5 w-full overflow-hidden rounded-full border border-line bg-surface-sunken"
                    >
                      <div className="hatch absolute inset-y-0 left-0 opacity-80" style={{ width: `${rate * 100}%` }} />
                    </div>
                  </li>
                );
              })}
            </ul>
            <p className="mt-4 text-xs leading-relaxed text-muted">{missing?.note}</p>
          </Card>
        )}

        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Card className="p-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Episodes without a diff</p>
            <p className="mt-1 font-mono text-xl font-semibold text-ink">
              {formatNumber(missing?.episodes_without_diff)}
            </p>
            <p className="mt-1 text-xs leading-snug text-muted">
              A shallow clone has no merge commit for some pull requests, so no file-level change is visible.
            </p>
          </Card>
          <Card className="p-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              Episodes without release corroboration
            </p>
            <p className="mt-1 font-mono text-xl font-semibold text-ink">
              {formatNumber(missing?.episodes_without_release_corroboration)}
            </p>
            <p className="mt-1 text-xs leading-snug text-muted">
              Merged, but nothing independently confirms users saw it. These can never reach the top band.
            </p>
          </Card>
          <Card className="p-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              Contributors below the evidence bar
            </p>
            <p className="mt-1 font-mono text-xl font-semibold text-ink">
              {formatNumber(missing?.engineers_below_evidence_bar)}
            </p>
            <p className="mt-1 text-xs leading-snug text-muted">
              Excluded from ranking. A statement about the window, not about them.
            </p>
          </Card>
        </div>
      </section>

      {/* Disabled capabilities. */}
      <section aria-labelledby="disabled-heading">
        <SectionHeading
          id="disabled-heading"
          title="Capabilities disabled by missing inputs"
          description="Where a Phase 1 table is absent, the analysis it feeds is switched off rather than approximated. These parts of the dashboard read 'not assessable' for everyone, equally."
        />
        {disabled.length === 0 ? (
          <Callout tone="neutral">
            <p>Every input table Phase 2 needs was present. No analysis was disabled in this run.</p>
          </Callout>
        ) : (
          <TableScroll label="Disabled capabilities">
            <DataTable>
              <caption className="sr-only">
                Missing Phase 1 input tables and the analysis each absence disables.
              </caption>
              <thead>
                <tr>
                  <Th>Missing input</Th>
                  <Th>What it disables</Th>
                </tr>
              </thead>
              <tbody>
                {disabled.map(([table, effect]) => (
                  <tr key={table}>
                    <Th scope="row" className="bg-surface normal-case tracking-normal">
                      <code className="font-mono text-ink">{table}</code>
                    </Th>
                    <Td>{effect}</Td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </TableScroll>
        )}
      </section>

      {/* Known gaps. */}
      <section aria-labelledby="gaps-heading">
        <SectionHeading
          id="gaps-heading"
          title="Known structural gaps"
          description="Properties of the data source itself. These do not go away with a longer run."
        />
        {coverage.known_gaps.length === 0 ? (
          <EmptyState title="No structural gap was recorded for this run." />
        ) : (
          <ul className="space-y-3">
            {coverage.known_gaps.map((g) => (
              <li key={g.gap}>
                <Card>
                  <div className="mb-1.5 flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-ink">{titleize(g.gap)}</h3>
                    {g.severity ? (
                      <Badge tone={SEVERITY_TONE[g.severity] ?? 'neutral'}>{humanize(g.severity)}</Badge>
                    ) : null}
                  </div>
                  {g.detail ? <p className="text-sm leading-relaxed text-ink-soft">{g.detail}</p> : null}
                  {g.consequence ? (
                    <p className="mt-1.5 text-xs leading-relaxed text-muted">
                      <strong className="text-ink-soft">Consequence: </strong>
                      {g.consequence}
                    </p>
                  ) : null}
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Validation programme. */}
      <section aria-labelledby="validation-heading">
        <SectionHeading
          id="validation-heading"
          title="Validation programme"
          description="Ten checks, four of which require a human. The package refuses to mark itself publishable until every one of them is signed off — that gate is a design decision, not a bug."
        />
        <Card>
          <dl className="mb-4 flex flex-wrap gap-x-6 gap-y-2">
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Status</dt>
              <dd className="font-mono text-sm text-ink">{validation?.status ?? manifest.validation_status}</dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Publishable</dt>
              <dd className="font-mono text-sm text-ink">{String(validation?.publishable ?? manifest.publishable)}</dd>
            </div>
          </dl>

          {(validation?.items ?? []).length > 0 ? (
            <TableScroll label="Validation items">
              <DataTable>
                <thead>
                  <tr>
                    <Th>Check</Th>
                    <Th>Status</Th>
                    <Th>Detail</Th>
                  </tr>
                </thead>
                <tbody>
                  {(validation?.items ?? []).map((item, i) => {
                    const row = item as Record<string, unknown>;
                    const status = String(row.status ?? '');
                    return (
                      <tr key={`${row.item ?? i}`}>
                        <Th scope="row" className="bg-surface normal-case tracking-normal text-ink">
                          {titleize(String(row.item ?? ''))}
                        </Th>
                        <Td>
                          <Badge
                            tone={status === 'pass' ? 'ok' : status === 'fail' ? 'danger' : 'warn'}
                          >
                            {humanize(status)}
                          </Badge>
                        </Td>
                        <Td className="max-w-[28rem] text-xs">
                          {Object.entries(row)
                            .filter(([k]) => k !== 'item' && k !== 'status')
                            .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
                            .join(' · ') || '—'}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </DataTable>
            </TableScroll>
          ) : (
            <EmptyState title="No validation items were published for this run." />
          )}

          {(validation?.publishable_blockers ?? []).length > 0 ? (
            <div className="mt-4">
              <h3 className="mb-2 text-sm font-semibold text-ink">Outstanding human sign-off</h3>
              <ul className="space-y-2">
                {(validation?.publishable_blockers ?? []).map((b, i) => {
                  const row = b as Record<string, unknown>;
                  return (
                    <li key={`${row.item ?? i}`} className="rounded-lg border border-[#d8b263] bg-[#fbf0d9] p-3">
                      <p className="text-sm font-medium text-ink">{titleize(String(row.item ?? ''))}</p>
                      <p className="mt-0.5 font-mono text-xs text-warn">
                        {String(row.status ?? 'pending')}
                        {row.queue_file ? ` · queue: ${String(row.queue_file)}` : ''}
                      </p>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
        </Card>
      </section>

      {/* Per-stage summaries. */}
      {coverage.summaries ? (
        <section aria-labelledby="summaries-heading">
          <SectionHeading
            id="summaries-heading"
            title="Per-stage statistics"
            description="What each pipeline stage produced, including everything it discarded and why."
          />
          <details className="group card p-0">
            <summary className="flex min-h-14 cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-ink [&::-webkit-details-marker]:hidden">
              <span aria-hidden="true" className="text-muted transition-transform group-open:rotate-90">
                ▸
              </span>
              Full stage-by-stage report
            </summary>
            <div className="border-t border-line p-4">
              <JsonSpec value={coverage.summaries} />
            </div>
          </details>
        </section>
      ) : null}

      {/* Correction pathway. */}
      <section aria-labelledby="correction-heading">
        <SectionHeading id="correction-heading" title="Found something wrong?" />
        <Callout tone="accent">
          <p>
            {(coverage.limitations.correction_pathway as { instructions?: string } | null)?.instructions ??
              bundle.correctionPathway?.instructions ??
              'Every claim on this dashboard carries a claim id. Quote it when reporting an error.'}
          </p>
          <p className="mt-2 text-xs">
            Every rendered sentence has an <strong>Evidence</strong> button beside it. Open it, copy the claim id, and
            quote that — it identifies the exact assertion and the artifacts it rests on.
          </p>
        </Callout>
      </section>
    </div>
  );
}
