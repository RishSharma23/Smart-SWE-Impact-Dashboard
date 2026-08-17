import Link from 'next/link';
import * as React from 'react';

import { Card } from '@/components/primitives';

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-12 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">404</p>
      <h1 className="mt-2 text-2xl font-semibold text-ink">That page is not part of this run</h1>
      <p className="mt-3 text-sm leading-relaxed text-ink-soft">
        The dashboard is generated statically from one Phase 2 export. If you followed a link from an earlier run, the
        contributor or episode it pointed at may not exist in this one — nothing is invented to fill the gap.
      </p>
      <Card className="mt-6 text-left">
        <ul className="space-y-2 text-sm">
          <li>
            <Link href="/" className="font-medium text-d2 underline underline-offset-2">
              Overview and the top five
            </Link>
          </li>
          <li>
            <Link href="/engineers/" className="font-medium text-d2 underline underline-offset-2">
              Every contributor in this run
            </Link>
          </li>
          <li>
            <Link href="/methodology/" className="font-medium text-d2 underline underline-offset-2">
              How impact is defined and measured
            </Link>
          </li>
          <li>
            <Link href="/coverage/" className="font-medium text-d2 underline underline-offset-2">
              What this run could not see
            </Link>
          </li>
        </ul>
      </Card>
    </div>
  );
}
