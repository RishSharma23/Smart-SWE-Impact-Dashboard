import type { Metadata, Viewport } from 'next';
import * as React from 'react';

import { AppShell } from '@/components/AppShell';
import { BuildMetadataFooter } from '@/components/BuildMetadataFooter';
import { ScenarioProvider } from '@/components/ScenarioProvider';
import { loadBundle } from '@/lib/data';
import { toScenarioMeta } from '@/lib/scenario';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'PostHog Observable Engineering Impact',
    template: '%s · PostHog Observable Engineering Impact',
  },
  description:
    'Explainable, evidence-linked analysis of observable engineering impact in the public PostHog repository. Not a productivity tracker.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#f3f2ec',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const bundle = loadBundle();
  const { manifest, provenance, coverage } = bundle;

  return (
    <html lang="en">
      <body>
        <ScenarioProvider
          scenarios={bundle.rankings.scenarios.map(toScenarioMeta)}
          defaultScenario={bundle.rankings.default_scenario}
        >
          <AppShell
            meta={{
              title: manifest.title,
              subtitle: manifest.subtitle ?? null,
              windowStart: manifest.window.start,
              windowEnd: manifest.window.end,
              lookbackDays: manifest.window.lookback_days ?? null,
              generatedAt: manifest.generated_at,
              headSha: manifest.source.analyzed_head_sha,
              repositoryUrl: manifest.source.repository_url,
              methodologyVersion: manifest.methodology_version,
            }}
          >
            {children}
          </AppShell>
        </ScenarioProvider>

        <BuildMetadataFooter
          meta={{
            generatedAt: manifest.generated_at,
            stagedAt: provenance.staged_at,
            headSha: manifest.source.analyzed_head_sha,
            repositoryUrl: manifest.source.repository_url,
            isShallowClone: Boolean(manifest.source.is_shallow_clone),
            windowStart: manifest.window.start,
            windowEnd: manifest.window.end,
            manifestVersion: manifest.manifest_version,
            methodologyVersion: manifest.methodology_version,
            sourceDir: provenance.source_dir,
            isFixture: provenance.is_fixture,
            fileCount: provenance.file_count,
            totalBytes: provenance.total_bytes,
            exportMode: manifest.export_mode ?? null,
            counts: manifest.counts,
            validationStatus: manifest.validation_status,
            publishable: manifest.publishable,
            safetyScan: manifest.safety_scan?.status ?? null,
            correctionInstructions:
              (coverage.limitations.correction_pathway as { instructions?: string } | null)?.instructions ??
              bundle.correctionPathway?.instructions ??
              null,
          }}
        />
      </body>
    </html>
  );
}
