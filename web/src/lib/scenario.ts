import type { Scenario } from './schema';

/**
 * The scenario fields the client selector needs. Kept out of the client module
 * so server components can build the list without pulling the provider in.
 */
export interface ScenarioMeta {
  scenario: string;
  label: string;
  description: string | null;
  available: boolean;
  unavailable_reason: string | null;
  remedy: string | null;
  note: string | null;
  alternatives: number | null;
  excluded_insufficient_evidence: number | null;
  weights: Record<string, number> | null;
}

export function toScenarioMeta(s: Scenario): ScenarioMeta {
  return {
    scenario: s.scenario,
    label: s.label ?? s.scenario,
    description: s.description ?? null,
    available: s.available,
    unavailable_reason: s.unavailable_reason ?? null,
    remedy: s.remedy ?? null,
    note: s.note ?? null,
    alternatives: s.alternatives ?? null,
    excluded_insufficient_evidence: s.excluded_insufficient_evidence ?? null,
    weights: s.weights ?? null,
  };
}
