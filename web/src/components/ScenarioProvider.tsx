'use client';

import * as React from 'react';

import type { ScenarioMeta } from '@/lib/scenario';

/**
 * The selected ranking scenario, shared between the header selector and every
 * page that renders a position. Only *available* scenarios can be selected —
 * unavailable ones stay visible and disabled with their reason, because the
 * point is that the reader can see what was not measurable.
 */
export type { ScenarioMeta };

interface Ctx {
  scenarios: ScenarioMeta[];
  selected: string;
  select: (name: string) => void;
  meta: ScenarioMeta | undefined;
  /** Bumped on every change so live regions announce it. */
  announcement: string;
}

const ScenarioContext = React.createContext<Ctx | null>(null);

const STORAGE_KEY = 'impact.scenario';

export function ScenarioProvider({
  scenarios,
  defaultScenario,
  children,
}: {
  scenarios: ScenarioMeta[];
  defaultScenario: string;
  children: React.ReactNode;
}) {
  const selectable = React.useMemo(() => scenarios.filter((s) => s.available), [scenarios]);
  const initial = selectable.some((s) => s.scenario === defaultScenario)
    ? defaultScenario
    : (selectable[0]?.scenario ?? defaultScenario);

  const [selected, setSelected] = React.useState(initial);
  const [announcement, setAnnouncement] = React.useState('');

  // Restore a previous choice after hydration so the server HTML and the first
  // client render always agree (no hydration warning).
  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored && selectable.some((s) => s.scenario === stored) && stored !== selected) {
        setSelected(stored);
      }
    } catch {
      /* storage unavailable — the default is fine */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = React.useCallback(
    (name: string) => {
      const meta = selectable.find((s) => s.scenario === name);
      if (!meta) return;
      setSelected(name);
      setAnnouncement(`Ranking scenario changed to ${meta.label}. Positions and explanations updated.`);
      try {
        window.localStorage.setItem(STORAGE_KEY, name);
      } catch {
        /* ignore */
      }
    },
    [selectable],
  );

  const value = React.useMemo<Ctx>(
    () => ({
      scenarios,
      selected,
      select,
      meta: scenarios.find((s) => s.scenario === selected),
      announcement,
    }),
    [scenarios, selected, select, announcement],
  );

  return (
    <ScenarioContext.Provider value={value}>
      {children}
      <p aria-live="polite" role="status" className="sr-only">
        {announcement}
      </p>
    </ScenarioContext.Provider>
  );
}

export function useScenario(): Ctx {
  const ctx = React.useContext(ScenarioContext);
  if (!ctx) throw new Error('useScenario must be used inside <ScenarioProvider>');
  return ctx;
}
