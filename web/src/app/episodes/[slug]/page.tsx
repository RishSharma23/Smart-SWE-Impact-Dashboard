import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import * as React from 'react';

import { Claim, EvidenceTrigger } from '@/components/Claim';
import { CounterevidencePanel } from '@/components/CounterevidencePanel';
import { ImpactEpisodeCard, ReleaseQualifier } from '@/components/ImpactEpisodeCard';
import { PropagationMiniGraph } from '@/components/PropagationMiniGraph';
import { RoleAttributionList } from '@/components/RoleAttributionList';
import { SourceLink } from '@/components/SourceLink';
import { Timeline } from '@/components/Timeline';
import { EvidenceChip } from '@/components/EvidenceChip';
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
import { loadBundle, slugFor } from '@/lib/data';
import {
  bandName,
  cn,
  dimensionLabel,
  formatDate,
  formatNumber,
  gradeFromCorroboration,
  humanize,
  REACHABILITY_LABEL,
  sortDimensions,
  statusLabel,
  titleize,
} from '@/lib/ui';
import { episodeView } from '@/lib/viewmodel';

export const dynamicParams = false;

export function generateStaticParams() {
  const bundle = loadBundle();
  return [...bundle.episodePageIds].map((id) => ({ slug: slugFor(id) }));
}

function resolve(slug: string) {
  const bundle = loadBundle();
  for (const id of bundle.episodePageIds) {
    if (slugFor(id) === slug) return { bundle, episodeId: id };
  }
  return null;
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const found = resolve(slug);
  if (!found) return { title: 'Episode not found' };
  const ep = found.bundle.episodesById.get(found.episodeId)!;
  return { title: ep.title ?? 'Impact episode', description: `Problem, intervention, observable result and sources for the impact episode "${ep.title}".` };
}

export default async function EpisodePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const found = resolve(slug);
  if (!found) notFound();

  const view = episodeView(found.bundle, found.episodeId);
  if (!view) notFound();

  const { episode, claims, dimensionClaims, participantClaims, artifacts, engineerSlugs } = view;
  const prop = episode.analytics?.propagation ?? null;
  const novelty = episode.analytics?.novelty ?? null;
  const corrective = episode.analytics?.corrective_burden ?? null;
  const mergedOnly = episode.release_corroboration !== 'corroborated';

  return (
    <div className="space-y-10">
      <nav aria-label="Breadcrumb" className="text-xs text-muted">
        <Link href="/" className="underline decoration-line-strong underline-offset-2 hover:text-ink">
          Overview
        </Link>
        <span className="mx-1.5">/</span>
        <span className="text-ink-soft">Impact episode</span>
      </nav>

      <header>
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <Badge tone="outline">{statusLabel(episode.status)}</Badge>
          <ReleaseQualifier value={episode.release_corroboration ?? null} />
          {episode.has_ai_co_author ? <Badge tone="neutral">AI co-author trailer present</Badge> : null}
          {episode.touches_enterprise_licensed_code ? <Badge tone="warn">Touches enterprise-licensed code</Badge> : null}
        </div>
        <h1 className="text-3xl font-semibold leading-tight text-ink">{episode.title ?? 'Impact episode'}</h1>
        {/* The title is a convenience copy of the claim; render the claim only
            when it says something the heading does not. */}
        {claims.title && claims.title.text.trim() !== (episode.title ?? '').trim() ? (
          <Claim claim={claims.title} className="mt-3 max-w-3xl" tone="lead" />
        ) : claims.title ? (
          <p className="mt-2">
            <EvidenceTrigger claim={claims.title} label="Sources for this episode" />
          </p>
        ) : null}

        {mergedOnly ? (
          <Callout tone="warn" title="Merged is not released" className="mt-4 max-w-3xl">
            <p>
              This work landed on the default branch. Nothing in the public record independently confirms that users saw
              it — no changelog entry, no documentation change, no feature-flag removal. Read every statement below as
              &ldquo;merged&rdquo;, not &ldquo;shipped to users&rdquo;.
            </p>
          </Callout>
        ) : null}
      </header>

      {/* The narrative arc. */}
      <section aria-labelledby="narrative-heading">
        <SectionHeading
          id="narrative-heading"
          title="Problem, intervention, observable result"
          description="Each step below is a claim with artifacts behind it. Author claims are labelled separately from corroborated observations."
        />
        <Card>
          <Timeline
            steps={[
              {
                key: 'problem',
                label: 'Problem',
                date: episode.started_at,
                tone: 'muted',
                detail: claims.problem ? (
                  <Claim claim={claims.problem} />
                ) : (
                  <p className="text-sm italic text-unknown">
                    No problem statement could be traced to an artifact. Linked issues are absent from this run.
                  </p>
                ),
              },
              {
                key: 'intervention',
                label: 'Intervention',
                tone: 'accent',
                detail: claims.intervention ? (
                  <Claim claim={claims.intervention} />
                ) : (
                  <p className="text-sm italic text-unknown">Not observed in public data.</p>
                ),
              },
              {
                key: 'outcome',
                label: 'Observable result',
                date: episode.ended_at,
                tone: mergedOnly ? 'warn' : 'default',
                detail: (
                  <>
                    {claims.outcome ? (
                      <Claim claim={claims.outcome} />
                    ) : (
                      <p className="text-sm italic text-unknown">Not observed in public data.</p>
                    )}
                    {episode.status_reasons.length > 0 ? (
                      <ul className="mt-1.5 space-y-0.5">
                        {episode.status_reasons.map((r) => (
                          <li key={r} className="text-xs text-muted">
                            {r}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </>
                ),
              },
              {
                key: 'release',
                label: 'Release corroboration',
                tone: mergedOnly ? 'warn' : 'default',
                detail:
                  episode.release_evidence.length > 0 ? (
                    <ul className="space-y-1">
                      {episode.release_evidence.map((e, i) => (
                        <li key={i} className="flex flex-wrap items-baseline gap-2 text-sm text-ink-soft">
                          <EvidenceChip grade="corroborated" label={humanize(e.kind)} />
                          <span>{e.detail}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-ink-soft">
                      <EvidenceChip grade="unknown" /> No independent release signal was found. The change is merged;
                      whether it reached users is not observable here.
                    </p>
                  ),
              },
              {
                key: 'propagation',
                label: 'Propagation',
                detail: prop ? (
                  <p className="text-sm text-ink-soft">
                    Reached {formatNumber(prop.reach_file_count)} files and{' '}
                    {formatNumber(prop.reach_pr_count)} downstream pull requests across{' '}
                    {formatNumber(prop.distinct_component_penetration)} components, with{' '}
                    {formatNumber(prop.distinct_downstream_authors)} other authors building on it. Full measurements
                    below.
                  </p>
                ) : (
                  <p className="text-sm italic text-unknown">Propagation was not measured for this episode.</p>
                ),
              },
              {
                key: 'durability',
                label: 'Durability',
                tone: prop?.persistence_detected ? 'default' : 'muted',
                detail: prop ? (
                  <p className="text-sm text-ink-soft">
                    {prop.persistence_detected
                      ? 'Still attracting downstream change, so time decay is reduced.'
                      : 'No persistence signal — reach is discounted by the standard time decay.'}{' '}
                    Effective decay factor {prop.effective_decay_factor?.toFixed(3) ?? '—'} at{' '}
                    {prop.source_age_days?.toFixed(0) ?? '—'} days of age.
                  </p>
                ) : (
                  <p className="text-sm italic text-unknown">Not assessable.</p>
                ),
              },
            ]}
          />
        </Card>
      </section>

      {/* Facts. */}
      <section aria-labelledby="facts-heading">
        <h2 id="facts-heading" className="sr-only">
          Episode facts
        </h2>
        <Card>
          <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
            <KeyValue label="Window">
              <span className="font-mono">
                {formatDate(episode.started_at)} → {formatDate(episode.ended_at)}
              </span>
            </KeyValue>
            <KeyValue label="Duration">
              <span className="font-mono">
                {episode.duration_days ? `${episode.duration_days.toFixed(1)} days` : '—'}
              </span>
            </KeyValue>
            <KeyValue
              label="Reachability"
              hint={episode.reachability_band === 'unknown' ? 'no import parser for this language' : undefined}
            >
              {episode.reachability_band
                ? (REACHABILITY_LABEL[episode.reachability_band] ?? episode.reachability_band)
                : '—'}
            </KeyValue>
            <KeyValue
              label="Cluster confidence"
              hint={episode.cluster_confidence_reasons[0] ?? undefined}
            >
              <span className="font-mono">{episode.cluster_confidence?.toFixed(2) ?? '—'}</span>
            </KeyValue>
            <KeyValue label="Components" className="sm:col-span-2">
              {episode.components.length ? (
                <span className="font-mono text-xs">{episode.components.join(' · ')}</span>
              ) : (
                <span className="italic text-unknown">none recorded</span>
              )}
            </KeyValue>
            <KeyValue label="Pull requests">
              <span className="font-mono text-xs">
                {episode.pr_numbers.length ? episode.pr_numbers.map((n) => `#${n}`).join(' ') : '—'}
              </span>
            </KeyValue>
            <KeyValue label="Feature flags">
              <span className="font-mono text-xs">
                {episode.feature_flag_keys.length ? episode.feature_flag_keys.join(', ') : '—'}
              </span>
            </KeyValue>
          </dl>

          {novelty?.novelty_class ? (
            <div className="mt-5 border-t border-line pt-4">
              <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">Novelty</p>
              <p className="text-sm text-ink">
                {titleize(novelty.novelty_class)}
                {novelty.rationale ? <span className="text-muted"> — {novelty.rationale}</span> : null}
              </p>
              {novelty.uncertainty?.length ? (
                <ul className="mt-1.5 space-y-0.5">
                  {novelty.uncertainty.map((u) => (
                    <li key={u} className="text-xs italic text-muted">
                      {u}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </Card>
      </section>

      {/* Dimension assessment. */}
      <section aria-labelledby="episode-dimensions-heading">
        <SectionHeading
          id="episode-dimensions-heading"
          title="How this episode was banded"
          description="Each dimension is banded 0–4 from the artifact classes present. No band can reach 3 without corroboration from a second, independent artifact class."
        />
        <ul className="grid gap-3 lg:grid-cols-2">
          {sortDimensions(episode.dimensions).map((d) => {
            const grade = gradeFromCorroboration(d.corroboration_status, d.is_unknown);
            return (
              <li key={d.dimension}>
                <Card className={cn('h-full', d.is_unknown && 'bg-surface-sunken/60')}>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-ink">{dimensionLabel(d.dimension)}</h3>
                    <span className="flex items-center gap-1.5">
                      <EvidenceChip grade={grade} />
                      {d.is_unknown || d.band === null ? (
                        <Badge tone="unknown">not assessable</Badge>
                      ) : (
                        <Badge tone={d.band >= 3 ? 'accent' : 'neutral'}>
                          band {d.band} · {d.band_label ?? bandName(d.band)}
                        </Badge>
                      )}
                    </span>
                  </div>

                  {d.is_unknown ? (
                    <p className="text-sm leading-relaxed text-unknown">
                      {d.unknown_reason ??
                        'The public record could not settle this dimension. That is an absent value, not a low one.'}
                    </p>
                  ) : (
                    <Claim claim={dimensionClaims[d.dimension]} />
                  )}

                  {d.artifact_classes.length > 0 ? (
                    <ul className="mt-2.5 flex flex-wrap gap-1">
                      {d.artifact_classes.map((c) => (
                        <li
                          key={c}
                          className="rounded border border-line-strong bg-surface px-1.5 py-0.5 font-mono text-[10px] text-muted"
                        >
                          {c}
                        </li>
                      ))}
                    </ul>
                  ) : null}

                  {d.confidence_reasons.length > 0 ? (
                    <ul className="mt-2 space-y-0.5">
                      {d.confidence_reasons.map((r) => (
                        <li key={r} className="text-xs leading-relaxed text-muted">
                          {r}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </Card>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Participants. */}
      <section aria-labelledby="participants-heading">
        <SectionHeading
          id="participants-heading"
          title={`Who did what (${episode.participants.length})`}
          description="Role-specific attribution with the artifact that establishes each role. Everyone with direct evidence appears here."
        />
        <RoleAttributionList
          participants={episode.participants}
          claimsByActor={participantClaims}
          slugsByActor={engineerSlugs}
        />
      </section>

      {/* Counterevidence. */}
      <section aria-labelledby="episode-counter-heading">
        <SectionHeading id="episode-counter-heading" title="Counterevidence" />
        <CounterevidencePanel items={episode.counterevidence} />
        {corrective && (corrective.unconfirmed_event_count || Object.keys(corrective.by_class ?? {}).length) ? (
          <Card className="mt-3">
            <h3 className="mb-2 text-sm font-semibold text-ink">Corrective burden</h3>
            <dl className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
              {Object.entries(corrective.by_class ?? {}).map(([k, v]) => (
                <div key={k}>
                  <dt className="font-mono uppercase tracking-wide text-muted">{k.replace(/_/g, ' ')}</dt>
                  <dd className="font-mono font-semibold text-ink">{v}</dd>
                </div>
              ))}
              <div>
                <dt className="font-mono uppercase tracking-wide text-muted">confirmed revert</dt>
                <dd className="font-mono font-semibold text-ink">{corrective.confirmed_revert ? 'yes' : 'no'}</dd>
              </div>
              <div>
                <dt className="font-mono uppercase tracking-wide text-muted">capped penalty</dt>
                <dd className="font-mono font-semibold text-ink">{corrective.capped_penalty?.toFixed(2) ?? '—'}</dd>
              </div>
            </dl>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              Follow-up work is classified before it is counted. &ldquo;Healthy iteration&rdquo; is normal engineering
              and carries no penalty; only confirmed reverts and repeated corrective churn do.
            </p>
          </Card>
        ) : null}
      </section>

      {/* Propagation detail. */}
      <section aria-labelledby="episode-propagation-heading">
        <SectionHeading
          id="episode-propagation-heading"
          title="Propagation and durability"
          description="Measured by walking the import graph forward from the changed files, with hub damping so that touching a popular utility does not look like broad reach."
        />
        <Card>
          <PropagationMiniGraph propagation={prop} reachabilityBand={episode.reachability_band} />
        </Card>
      </section>

      {/* Sources. */}
      <section aria-labelledby="sources-heading">
        <SectionHeading
          id="sources-heading"
          title={`Every source artifact (${artifacts.length})`}
          description="The complete artifact set this episode was built from. Every URL points at github.com."
        />
        {artifacts.length === 0 ? (
          <EmptyState title="No artifact could be resolved for this episode." />
        ) : (
          <TableScroll label="Source artifacts">
            <DataTable>
              <caption className="sr-only">Every artifact backing this episode, with its kind and link.</caption>
              <thead>
                <tr>
                  <Th>Kind</Th>
                  <Th>Artifact</Th>
                  <Th>Detail</Th>
                  <Th>Link</Th>
                </tr>
              </thead>
              <tbody>
                {artifacts.map((a) => (
                  <tr key={a.artifact_id}>
                    <Td>
                      <Badge tone="outline">{humanize(a.kind)}</Badge>
                    </Td>
                    <Th scope="row" className="max-w-[20rem] bg-surface normal-case tracking-normal text-ink">
                      {a.title ?? a.artifact_id}
                    </Th>
                    <Td className="max-w-[16rem] text-xs">{a.detail ?? '—'}</Td>
                    <Td>
                      {a.url ? (
                        <SourceLink href={a.url} className="text-xs">
                          {a.url.replace('https://github.com/', '')}
                        </SourceLink>
                      ) : (
                        <span className="text-xs italic text-unknown">no url</span>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </TableScroll>
        )}
      </section>

      {episode.sub_episode_links.length > 0 ? (
        <section aria-labelledby="sub-episode-heading">
          <SectionHeading
            id="sub-episode-heading"
            title="How these pull requests were connected"
            description="The evidence that made these separate pull requests one episode."
          />
          <ul className="space-y-2">
            {episode.sub_episode_links.map((l, i) => {
              const link = l as { child_pr?: number; parent_pr?: number; relation?: string; evidence?: string };
              return (
                <li key={i} className="card p-3 text-sm text-ink-soft">
                  <span className="font-mono text-ink">#{link.child_pr}</span>{' '}
                  <Badge tone="outline">{humanize(link.relation)}</Badge>{' '}
                  <span className="font-mono text-ink">#{link.parent_pr}</span>
                  {link.evidence ? <span className="ml-2 text-xs text-muted">— {link.evidence}</span> : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
