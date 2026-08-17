import type { Metadata } from 'next';
import * as React from 'react';

import { Claim } from '@/components/Claim';
import { EvidenceChipLegend } from '@/components/EvidenceChip';
import { JsonSpec } from '@/components/JsonSpec';
import { Badge, Callout, Card, DataTable, SectionHeading, TableScroll, Td, Th } from '@/components/primitives';
import { loadBundle } from '@/lib/data';
import { dimensionLabel, formatNumber, titleize } from '@/lib/ui';

export const metadata: Metadata = {
  title: 'Methodology',
  description:
    'How observable engineering impact is defined, how episodes are built, how the six bands work, and everything the method deliberately does not use.',
};

const TECHNICAL_SECTIONS = [
  ['rubric', 'The six-dimension rubric', 'Every band rule, in English. A band is earned by the artifact classes present, not by a magnitude.'],
  ['attribution', 'Attribution and shared credit', 'Which roles exist, what evidence each one requires, and how credit is categorised when several people contributed.'],
  ['outranking', 'Outranking configuration', 'Criterion weights, indifference/preference/veto thresholds, distillation and the scenario definitions.'],
  ['analytics', 'Analytics parameters', 'Decay half-life, hub damping, propagation caps and the corrective-burden classes.'],
  ['episode_construction', 'Episode construction', 'How pull requests are clustered into initiative arcs, and the guards that stop over-merging.'],
  ['eligibility', 'Eligibility', 'The evidence bar a contributor must clear before they can be ranked at all.'],
] as const;

export default function MethodologyPage() {
  const bundle = loadBundle();
  const { methodology, rankings, coverage, manifest } = bundle;
  const limitationClaims = coverage.limitations.claim_ids
    .map((id) => bundle.claimsById.get(id))
    .filter(Boolean);

  const scenario = rankings.scenarios.find((s) => s.available && s.weights) ?? rankings.scenarios[0];

  return (
    <div className="space-y-10">
      <header className="max-w-3xl">
        <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          methodology {methodology.methodology_version} · export schema {methodology.export_schema_version ?? manifest.manifest_version}
        </p>
        <h1 className="text-3xl font-semibold leading-tight text-ink">How this was measured</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">
          Everything on this page comes from <code className="font-mono text-sm">methodology.json</code> in the
          published data package. Nothing here is a UI decision.
        </p>
      </header>

      {/* Is this a productivity tracker? */}
      <section aria-labelledby="not-used-heading">
        <SectionHeading
          id="not-used-heading"
          title="Is this a productivity tracker? No."
          description="These are the measures the method deliberately does not use, at any stage, for any purpose."
        />
        <Card className="border-danger/40">
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {methodology.explicitly_not_used.map((x) => (
              <li key={x} className="flex items-baseline gap-2 text-sm text-ink-soft">
                <span aria-hidden="true" className="font-bold text-danger">
                  ✕
                </span>
                <span className="line-through decoration-line-strong">{x}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm leading-relaxed text-ink-soft">
            Activity is not impact. In a repository with widespread AI-assisted authorship, volume measures mostly
            capture tooling, not judgement. The subject of every sentence on this dashboard is the{' '}
            <strong>work</strong>, never the person&apos;s ability, effort or seniority.
          </p>
        </Card>
      </section>

      {/* Definitions. */}
      <section aria-labelledby="definition-heading">
        <SectionHeading id="definition-heading" title="What counts as impact" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <h3 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Impact definition</h3>
            <p className="text-sm leading-relaxed text-ink">{methodology.impact_definition}</p>
          </Card>
          <Card>
            <h3 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Unit of analysis</h3>
            <p className="text-sm leading-relaxed text-ink">{methodology.unit_of_analysis}</p>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              A pull request is a unit of process, not a unit of value. Clustering related pull requests into one
              episode is what makes it possible to ask what changed for users rather than how much was typed.
            </p>
          </Card>
        </div>
      </section>

      {/* The six dimensions. */}
      <section aria-labelledby="dimensions-method-heading">
        <SectionHeading
          id="dimensions-method-heading"
          title="The six dimensions"
          description="Each episode is banded 0–4 on each dimension. Bands are ordinal categories earned by evidence, not magnitudes on a ratio scale — which is why they are never summed into a score."
        />
        <TableScroll label="The six impact dimensions and their weights">
          <DataTable>
            <caption className="sr-only">
              The six dimensions with their weight in the {scenario?.label ?? 'default'} scenario and the outranking
              thresholds applied to each.
            </caption>
            <thead>
              <tr>
                <Th>Dimension</Th>
                <Th>Weight ({scenario?.label ?? 'default'})</Th>
                <Th>Indifference (q)</Th>
                <Th>Preference (p)</Th>
                <Th>Veto (v)</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(scenario?.weights ?? {}).map(([dim, w]) => {
                const t = scenario?.thresholds?.[dim];
                return (
                  <tr key={dim}>
                    <Th scope="row" className="bg-surface normal-case tracking-normal text-ink">
                      {dimensionLabel(dim)}
                    </Th>
                    <Td className="font-mono">{w.toFixed(3)}</Td>
                    <Td className="font-mono">{t?.q ?? '—'}</Td>
                    <Td className="font-mono">{t?.p ?? '—'}</Td>
                    <Td className="font-mono">{t?.v ?? '—'}</Td>
                  </tr>
                );
              })}
            </tbody>
          </DataTable>
        </TableScroll>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          <strong>q</strong> is the difference below which two engineers are treated as indifferent. <strong>p</strong>{' '}
          is the difference above which one is strictly preferred. <strong>v</strong> is the difference at which a
          single criterion vetoes an outranking relation no matter how the others fall.
        </p>
      </section>

      {/* Ranking method. */}
      <section aria-labelledby="ranking-method-heading">
        <SectionHeading id="ranking-method-heading" title="How the ranking is produced" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <div className="mb-2 flex flex-wrap gap-1.5">
              <Badge tone="accent">{rankings.method?.name ?? 'ELECTRE III'}</Badge>
              <Badge tone="outline">cross-checked with {rankings.method?.cross_check ?? 'PROMETHEE II'}</Badge>
            </div>
            <h3 className="mb-2 text-sm font-semibold text-ink">Why there is no score</h3>
            <p className="text-sm leading-relaxed text-ink-soft">{rankings.method?.why_not_a_score}</p>
          </Card>
          <Card>
            <h3 className="mb-2 text-sm font-semibold text-ink">Why tiers, and why incomparability</h3>
            <p className="text-sm leading-relaxed text-ink-soft">{rankings.method?.tiers_explained}</p>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              A second aggregation method is run over the same inputs. Where the two disagree, the dashboard says so —
              disagreement is information about how firm a position is, not noise to be averaged away.
            </p>
          </Card>
        </div>
      </section>

      {/* Scenarios. */}
      <section aria-labelledby="scenarios-heading">
        <SectionHeading
          id="scenarios-heading"
          title={`Ranking scenarios (${rankings.scenarios.length})`}
          description="One weighting is a hypothesis, not a finding. Scenarios test whether the same names survive a different, equally defensible set of priorities."
        />
        <ul className="grid gap-3 lg:grid-cols-2">
          {rankings.scenarios.map((s) => (
            <li key={s.scenario}>
              <Card className={s.available ? '' : 'border-dashed bg-surface-sunken/60'}>
                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">{s.label ?? s.scenario}</h3>
                  {s.available ? (
                    <Badge tone="ok">available</Badge>
                  ) : (
                    <Badge tone="unknown">not computed</Badge>
                  )}
                </div>
                <p className="font-mono text-[10px] text-muted">{s.scenario}</p>
                {s.description ? (
                  <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">{s.description}</p>
                ) : null}
                {s.note ? <p className="mt-1.5 text-xs italic text-muted">{s.note}</p> : null}
                {!s.available ? (
                  <>
                    <p className="mt-2 text-sm leading-relaxed text-warn">{s.unavailable_reason}</p>
                    {s.remedy ? (
                      <pre className="mt-2 overflow-x-auto rounded border border-line bg-surface p-2 font-mono text-[11px] text-ink-soft">
                        {s.remedy}
                      </pre>
                    ) : null}
                  </>
                ) : (
                  <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                    <div>
                      <dt className="inline font-mono uppercase tracking-wide">rankable: </dt>
                      <dd className="inline font-mono text-ink-soft">{formatNumber(s.alternatives)}</dd>
                    </div>
                    <div>
                      <dt className="inline font-mono uppercase tracking-wide">excluded: </dt>
                      <dd className="inline font-mono text-ink-soft">
                        {formatNumber(s.excluded_insufficient_evidence)}
                      </dd>
                    </div>
                  </dl>
                )}
              </Card>
            </li>
          ))}
        </ul>
      </section>

      {/* Evidence grading. */}
      <section aria-labelledby="evidence-method-heading">
        <SectionHeading
          id="evidence-method-heading"
          title="How evidence is graded"
          description="Corroboration is a hard gate, not a bonus: no dimension reaches the 'broad' band on a single source."
        />
        <Card>
          <EvidenceChipLegend />
        </Card>
      </section>

      {/* LLM role. */}
      <section aria-labelledby="llm-heading">
        <SectionHeading id="llm-heading" title="What the language model did" />
        <Callout tone={methodology.llm.available ? 'neutral' : 'accent'}>
          {methodology.llm.available ? (
            <>
              <p className="mb-2">
                Provider <strong>{methodology.llm.provider}</strong>, model{' '}
                <code className="font-mono text-xs">{methodology.llm.model}</code>.
              </p>
              <p>{methodology.llm.role}</p>
            </>
          ) : (
            <p>
              <strong>No language model ran.</strong> The optional semantic layer was not configured, so every result on
              this dashboard is deterministic. Even when it does run, the model only extracts and summarises structured
              evidence — <strong>it never produces the ranking</strong>.
            </p>
          )}
          {methodology.llm.note ? <p className="mt-2 text-xs text-muted">{methodology.llm.note}</p> : null}
        </Callout>
      </section>

      {/* Formulas. */}
      <section aria-labelledby="formulas-heading">
        <SectionHeading
          id="formulas-heading"
          title="The formulas, literally"
          description="Published so the arithmetic can be checked rather than trusted."
        />
        <Card>
          <dl className="space-y-3">
            {Object.entries(methodology.formulas).map(([name, formula]) => (
              <div key={name} className="min-w-0">
                <dt className="text-sm font-medium text-ink">{titleize(name)}</dt>
                <dd className="mt-1 overflow-x-auto rounded border border-line bg-surface-sunken px-2.5 py-2 font-mono text-xs leading-relaxed text-ink-soft">
                  {formula}
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      </section>

      {/* Technical detail. */}
      <section aria-labelledby="technical-heading">
        <SectionHeading
          id="technical-heading"
          title="Full technical configuration"
          description="Every rule and parameter that produced this run, straight from the package. Collapsed by default because it is reference material, not narrative."
        />
        <div className="space-y-3">
          {TECHNICAL_SECTIONS.map(([key, title, blurb]) => {
            const value = (methodology as unknown as Record<string, unknown>)[key];
            if (value === null || value === undefined) return null;
            return (
              <details key={key} className="group card p-0">
                <summary className="flex min-h-14 cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold text-ink [&::-webkit-details-marker]:hidden">
                  <span aria-hidden="true" className="text-muted transition-transform group-open:rotate-90">
                    ▸
                  </span>
                  {title}
                </summary>
                <div className="border-t border-line p-4">
                  <p className="mb-3 text-xs leading-relaxed text-muted">{blurb}</p>
                  <JsonSpec value={value} />
                </div>
              </details>
            );
          })}
        </div>
      </section>

      {/* Limitations. */}
      <section aria-labelledby="limits-heading">
        <SectionHeading
          id="limits-heading"
          title="Limitations"
          description={coverage.limitations.headline}
        />
        <Card>
          <ul className="space-y-2.5">
            {coverage.limitations.items.map((item) => (
              <li key={item} className="flex gap-2.5 text-sm leading-relaxed text-ink-soft">
                <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-warn" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Card>
        {limitationClaims.length > 0 ? (
          <div className="mt-4 space-y-2">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              The same limitations as traceable claims
            </h3>
            <Card>
              <div className="prose-editorial">
                {limitationClaims.map((c) => (
                  <Claim key={c!.claim_id} claim={c!} />
                ))}
              </div>
            </Card>
          </div>
        ) : null}
      </section>
    </div>
  );
}
