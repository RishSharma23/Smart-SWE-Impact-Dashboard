'use client';

import { Lock } from 'lucide-react';
import * as React from 'react';

import type { Stability } from '@/lib/schema';
import { cn } from '@/lib/ui';

import { ImpactTierBadge } from './ImpactTierBadge';
import { StabilityIndicator } from './StabilityIndicator';
import { useScenario } from './ScenarioProvider';

export interface ScenarioPositionRow {
  scenario: string;
  label: string;
  available: boolean;
  position: number | null;
  tier: number | null;
  crossCheckDelta: number | null;
  incomparableWith: string[];
  stability: Stability | null;
  unavailableReason: string | null;
}

/**
 * Where this contributor sits in every scenario. The row for the currently
 * selected scenario is highlighted, and unavailable scenarios are listed with
 * their reason rather than hidden.
 */
export function EngineerScenarioPosition({
  positions,
  login,
}: {
  positions: ScenarioPositionRow[];
  login: string;
}) {
  const { selected, select } = useScenario();

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-ink">Position by scenario</h3>
      <ul className="space-y-2">
        {positions.map((row) => {
          const isSelected = row.scenario === selected;
          return (
            <li
              key={row.scenario}
              className={cn(
                'rounded-lg border p-3',
                isSelected ? 'border-accent bg-accent-wash' : 'border-line bg-surface',
                !row.available && 'border-dashed bg-surface-sunken',
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                {row.available ? (
                  <button
                    type="button"
                    onClick={() => select(row.scenario)}
                    aria-pressed={isSelected}
                    className="min-h-9 rounded text-sm font-medium text-ink hover:underline"
                  >
                    {row.label}
                  </button>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-sm font-medium text-unknown">
                    <Lock aria-hidden="true" className="size-3.5" />
                    {row.label}
                  </span>
                )}

                {!row.available ? (
                  <span className="font-mono text-[11px] uppercase tracking-wide text-unknown">not computed</span>
                ) : row.position === null ? (
                  <span className="font-mono text-[11px] text-unknown">outside the ranked set</span>
                ) : (
                  <ImpactTierBadge tier={row.tier ?? 0} position={row.position} />
                )}
              </div>

              {!row.available && row.unavailableReason ? (
                <p className="mt-1.5 text-xs leading-relaxed text-muted">{row.unavailableReason}</p>
              ) : null}

              {row.available && row.position !== null ? (
                <>
                  {row.incomparableWith.length > 0 ? (
                    <p className="mt-1.5 text-xs leading-relaxed text-ink-soft">
                      Incomparable with {row.incomparableWith.join(', ')} — the evidence does not settle which ranks
                      higher.
                    </p>
                  ) : null}
                  {row.crossCheckDelta ? (
                    <p className="mt-1.5 text-xs leading-relaxed text-warn">
                      The cross-check method disagrees by {Math.abs(row.crossCheckDelta)}{' '}
                      {Math.abs(row.crossCheckDelta) === 1 ? 'position' : 'positions'}.
                    </p>
                  ) : null}
                  {isSelected && row.stability ? (
                    <StabilityIndicator stability={row.stability} compact className="mt-2" />
                  ) : null}
                </>
              ) : null}
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        A position without its weighting is not information. Selecting a scenario here changes the whole dashboard, so
        every claim about @{login} stays consistent with the weighting on screen.
      </p>
    </div>
  );
}
