import type { Metadata } from 'next';
import * as React from 'react';

import { CompareView } from '@/components/CompareView';
import { loadBundle } from '@/lib/data';
import { leadersByScenario, pairwiseByScenario } from '@/lib/viewmodel';

export const metadata: Metadata = {
  title: 'Compare',
  description:
    'Compare two top-five candidates on six evidence dimensions, with the published outranking trace for both directions.',
};

export default function ComparePage() {
  const bundle = loadBundle();

  return (
    <div className="space-y-8">
      <header className="max-w-3xl">
        <h1 className="text-3xl font-semibold leading-tight text-ink">Compare two candidates</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">
          Pick two of the top five and read why one outranks the other — or why neither does. Every comparison here is
          the same pairwise material that produced the ranking, published in full rather than summarised into a number.
        </p>
      </header>

      <CompareView leadersByScenario={leadersByScenario(bundle)} pairwiseByScenario={pairwiseByScenario(bundle)} />
    </div>
  );
}
