#!/usr/bin/env node
/**
 * Stage the Phase 2 static export into web/.data/ and verify its integrity.
 *
 * This runs before `next build`. It does three things and nothing else:
 *
 *   1. resolves which package to build against (real export vs. dev fixture),
 *   2. verifies every file's sha256 against dashboard_manifest.json,
 *   3. copies the package to web/.data/ and records the provenance.
 *
 * Shape/semantic validation is Zod's job and happens inside the Next build
 * (src/lib/data.ts), so a schema violation fails `next build` too.
 *
 * Env:
 *   IMPACT_DATA_DIR   path to the phase 3 package. Default: ../artifacts/phase3
 *                     falling back to ../docs/fixtures/phase3 when absent.
 *   IMPACT_ALLOW_FIXTURE=1  permit a fixture package (development only).
 *   IMPACT_PUBLISH_APPROVAL  free-text record of the human sign-off that the
 *                     export's own `publishable` gate is waiting on, e.g.
 *                     "Rish Sharma 2026-08-17: finalists and safety scan
 *                     reviewed". Set it only when a human really has looked.
 *                     Without it a `publishable: false` package still builds and
 *                     renders — it just carries the provisional banner.
 *   NODE_ENV=production     production build; a fixture package is rejected
 *                     unless IMPACT_ALLOW_FIXTURE=1 is also set.
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, '..');
const REPO = path.resolve(WEB, '..');
const DEST = path.join(WEB, '.data');

const APPROVED_MANIFEST_VERSIONS = ['1.0.0'];

const REQUIRED = [
  'dashboard_manifest.json',
  'rankings.json',
  'engineers.json',
  'episodes.json',
  'comparisons.json',
  'claims.json',
  'evidence.json',
  'methodology.json',
  'coverage.json',
  'indexes.json',
];

function fail(msg) {
  console.error(`\n  data build FAILED\n\n  ${msg}\n`);
  process.exit(1);
}

function sha256(file) {
  return createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    fail(`${file} is not readable JSON: ${err.message}`);
  }
}

// -- 1. resolve the source package ------------------------------------------

function resolveSource() {
  const explicit = process.env.IMPACT_DATA_DIR;
  const real = path.join(REPO, 'artifacts', 'phase3');
  const fixture = path.join(REPO, 'docs', 'fixtures', 'phase3');

  const candidates = explicit
    ? [path.resolve(REPO, explicit)]
    : [real, fixture];

  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, 'dashboard_manifest.json'))) return dir;
  }
  fail(
    `no phase 3 package found. Looked in:\n    ${candidates.join('\n    ')}\n\n` +
      `  Produce one with:  make p2-export\n` +
      `  Or point at the fixture:  IMPACT_DATA_DIR=docs/fixtures/phase3 npm run build`,
  );
}

// -- 2. integrity ------------------------------------------------------------

function verify(src, manifest) {
  if (!APPROVED_MANIFEST_VERSIONS.includes(manifest.manifest_version)) {
    fail(
      `unapproved manifest_version "${manifest.manifest_version}". ` +
        `This UI is built for ${APPROVED_MANIFEST_VERSIONS.join(', ')}. ` +
        `Bump APPROVED_MANIFEST_VERSIONS only after re-reading contracts/PHASE_3_CONTRACT.md.`,
    );
  }

  for (const name of REQUIRED) {
    if (!fs.existsSync(path.join(src, name))) fail(`required file missing from the package: ${name}`);
  }

  const files = manifest.files ?? {};
  if (Object.keys(files).length === 0) fail('dashboard_manifest.json declares no files');

  const bad = [];
  for (const [name, meta] of Object.entries(files)) {
    const abs = path.join(src, meta.path ?? name);
    if (!fs.existsSync(abs)) {
      bad.push(`${name}: declared in the manifest but absent on disk`);
      continue;
    }
    const actual = sha256(abs);
    if (meta.sha256 && actual !== meta.sha256) {
      bad.push(`${name}: sha256 ${actual.slice(0, 12)}… != manifest ${String(meta.sha256).slice(0, 12)}…`);
    }
    const bytes = fs.statSync(abs).size;
    if (meta.bytes && bytes !== meta.bytes) {
      bad.push(`${name}: ${bytes} bytes on disk, manifest says ${meta.bytes}`);
    }
  }
  if (bad.length) {
    fail(`the package does not match its manifest — a partial copy?\n\n    ${bad.join('\n    ')}`);
  }
}

// -- 3. the fixture gate -----------------------------------------------------

function gateFixture(manifest, src) {
  const isFixture = manifest.fixture === true;
  const allow = process.env.IMPACT_ALLOW_FIXTURE === '1';
  const isProd = process.env.NODE_ENV === 'production' || process.env.CI === 'true';

  if (isFixture && isProd && !allow) {
    fail(
      `this is the SYNTHETIC fixture package (${src}) and NODE_ENV=production.\n\n` +
        `  A production build must not ship fixture data. Either:\n` +
        `    - run  make p2-export  and build against artifacts/phase3/, or\n` +
        `    - set IMPACT_ALLOW_FIXTURE=1 to build a deliberately marked DEMO site.`,
    );
  }
  return { isFixture, demoAcknowledged: allow };
}

// -- run ---------------------------------------------------------------------

const src = resolveSource();
const manifest = readJson(path.join(src, 'dashboard_manifest.json'));
verify(src, manifest);
const { isFixture, demoAcknowledged } = gateFixture(manifest, src);

fs.rmSync(DEST, { recursive: true, force: true });
fs.mkdirSync(DEST, { recursive: true });
fs.cpSync(src, DEST, { recursive: true, filter: (p) => !path.basename(p).startsWith('.') });

const approval = (process.env.IMPACT_PUBLISH_APPROVAL ?? '').trim();

const provenance = {
  source_dir: path.relative(REPO, src),
  /**
   * `publishable` in the manifest is Phase 2's automated verdict and is never
   * rewritten here — the export's record stays exactly as Phase 2 wrote it.
   * A human override is recorded alongside it instead, so the page can be
   * honest about which of the two the reader is looking at.
   */
  export_publishable: manifest.publishable === true,
  publish_approval: approval || null,
  is_fixture: isFixture,
  demo_acknowledged: demoAcknowledged,
  manifest_version: manifest.manifest_version,
  methodology_version: manifest.methodology_version,
  generated_at: manifest.generated_at,
  staged_at: new Date().toISOString(),
  file_count: Object.keys(manifest.files ?? {}).length,
  total_bytes: Object.values(manifest.files ?? {}).reduce((n, f) => n + (f.bytes ?? 0), 0),
};
fs.writeFileSync(path.join(DEST, '_provenance.json'), JSON.stringify(provenance, null, 2));

const mb = (provenance.total_bytes / 1024 / 1024).toFixed(2);
console.log(
  `  data  ${provenance.source_dir}  ->  web/.data  ` +
    `(${provenance.file_count} files, ${mb} MB, schema ${manifest.manifest_version}` +
    `${isFixture ? ', FIXTURE/DEMO' : ''}` +
    `${manifest.publishable === false ? `, export publishable=false${approval ? ' + human approval on record' : ''}` : ''})`,
);
if (manifest.publishable === false && !approval) {
  console.log(
    '  note  this package has not passed Phase 2\'s human-review gate; the site will\n' +
      '        carry the provisional banner. Set IMPACT_PUBLISH_APPROVAL to record a sign-off.',
  );
}
