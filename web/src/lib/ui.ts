import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// -- dimensions --------------------------------------------------------------

export const DIMENSION_ORDER = [
  'product_outcome',
  'reliability_risk',
  'engineering_leverage',
  'decision_quality',
  'propagation_durability',
  'collaborative_amplification',
] as const;

export const DIMENSION_LABEL: Record<string, string> = {
  product_outcome: 'Product outcome',
  reliability_risk: 'Reliability & risk',
  engineering_leverage: 'Engineering leverage',
  decision_quality: 'Decision quality',
  propagation_durability: 'Propagation & durability',
  collaborative_amplification: 'Collaborative amplification',
};

export const DIMENSION_SHORT: Record<string, string> = {
  product_outcome: 'Product',
  reliability_risk: 'Reliability',
  engineering_leverage: 'Leverage',
  decision_quality: 'Decisions',
  propagation_durability: 'Durability',
  collaborative_amplification: 'Collaboration',
};

export const DIMENSION_COLOR: Record<string, string> = {
  product_outcome: 'var(--color-d1)',
  reliability_risk: 'var(--color-d2)',
  engineering_leverage: 'var(--color-d3)',
  decision_quality: 'var(--color-d4)',
  propagation_durability: 'var(--color-d5)',
  collaborative_amplification: 'var(--color-d6)',
};

export function dimensionLabel(key: string): string {
  return DIMENSION_LABEL[key] ?? humanize(key);
}

export function dimensionColor(key: string): string {
  return DIMENSION_COLOR[key] ?? 'var(--color-muted)';
}

export function sortDimensions<T extends { dimension: string }>(rows: T[]): T[] {
  const order = new Map(DIMENSION_ORDER.map((d, i) => [d as string, i]));
  return rows.slice().sort((a, b) => (order.get(a.dimension) ?? 99) - (order.get(b.dimension) ?? 99));
}

/**
 * Portfolio values live on the same 0-4 band scale as episode bands.
 * These are ORDINAL BANDS, never a score: the UI shows the band name first and
 * the number only as a secondary, clearly-labelled detail.
 */
export const BAND_SCALE_MAX = 4;

export const BAND_NAMES = ['none observed', 'narrow', 'material', 'broad', 'exceptional'] as const;

export function bandName(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'not assessable';
  const idx = Math.max(0, Math.min(BAND_NAMES.length - 1, Math.round(value)));
  return BAND_NAMES[idx];
}

// -- text --------------------------------------------------------------------

export function humanize(key: string | null | undefined): string {
  if (!key) return '';
  const s = key.replace(/[_\-.]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function titleize(key: string | null | undefined): string {
  if (!key) return '';
  return key
    .replace(/[_\-.]+/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** "not observed in public data", never "none". */
export const ABSENT = 'Not observed in public data';

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })}, ${d
    .toISOString()
    .slice(11, 16)} UTC`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toLocaleString('en-GB');
}

export function formatPercent(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return `${(n * 100).toFixed(digits)}%`;
}

export function shortSha(sha: string | null | undefined, n = 10): string {
  if (!sha) return '—';
  return sha.slice(0, n);
}

// -- domain vocabulary -------------------------------------------------------

export const STATUS_LABEL: Record<string, string> = {
  shipped_observable: 'Merged to default branch',
  partial_or_behind_flag: 'Partial or behind a flag',
  reverted: 'Reverted',
  superseded: 'Superseded',
  maintenance: 'Maintenance',
  exploratory: 'Exploratory',
  unknown: 'Status unknown',
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return 'Status unknown';
  return STATUS_LABEL[status] ?? humanize(status);
}

export const SHARE_LABEL: Record<string, string> = {
  primary: 'Primary credit',
  material: 'Material credit',
  supporting: 'Supporting credit',
  unclear: 'Credit unclear',
};

export function shareLabel(s: string | null | undefined): string {
  if (!s) return 'Credit unclear';
  return SHARE_LABEL[s] ?? humanize(s);
}

export const ROLE_LABEL: Record<string, string> = {
  core_implementer: 'Core implementer',
  originator: 'Originator',
  decision_shaper: 'Decision shaper',
  risk_preventer: 'Risk preventer',
  rollout_sustainer: 'Rollout sustainer',
  enabler: 'Enabler',
  reviewer: 'Reviewer',
};

export function roleLabel(r: string | null | undefined): string {
  if (!r) return '';
  return ROLE_LABEL[r] ?? humanize(r);
}

export const REACHABILITY_LABEL: Record<string, string> = {
  local: 'Local to one module',
  component: 'One component',
  cross_product: 'Across products',
  platform_wide: 'Platform-wide',
  unknown: 'Reach not assessable',
};

/**
 * Evidence chips. The five categories in the Phase 3 brief, mapped onto what
 * Phase 2 actually publishes.
 */
export type EvidenceGrade = 'direct' | 'corroborated' | 'inferred' | 'counterevidence' | 'unknown';

export const EVIDENCE_CHIP: Record<EvidenceGrade, { label: string; description: string }> = {
  direct: {
    label: 'Direct',
    description: 'The artifact itself is the evidence — a merged PR, a review comment, a removed flag.',
  },
  corroborated: {
    label: 'Corroborated',
    description: 'Two independent artifact classes agree. Required before a dimension can reach the "broad" band.',
  },
  inferred: {
    label: 'Inferred',
    description: 'Derived from ordering, graph reachability or thread resolution. Evidence, not proof.',
  },
  counterevidence: {
    label: 'Counterevidence',
    description: 'Evidence that argues against the claim. Always shown next to the claim it weakens.',
  },
  unknown: {
    label: 'Not assessable',
    description: 'The public data could not settle this. Not a low value — an absent one.',
  },
};

export function gradeFromCorroboration(status: string | null | undefined, isUnknown?: boolean): EvidenceGrade {
  if (isUnknown) return 'unknown';
  switch (status) {
    case 'corroborated':
      return 'corroborated';
    case 'single_source':
      return 'direct';
    case 'inferred':
    case 'uncorroborated':
      return 'inferred';
    default:
      return status ? 'direct' : 'unknown';
  }
}

export function tierLabel(tier: number): string {
  return `Tier ${tier}`;
}

export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] ?? s[v] ?? s[0]);
}
