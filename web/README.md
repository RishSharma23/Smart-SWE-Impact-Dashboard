# PostHog Observable Engineering Impact — dashboard

A public, read-only, statically generated dashboard over the Phase 2 export. No
server, no database, no runtime model call, no authentication.

Live: <https://rishsharma23.github.io/Smart-SWE-Impact-Dashboard/>

## Local setup

```bash
cd web
npm ci                     # Node 22 (see .nvmrc), lockfile committed
npm run dev                # http://localhost:3000
```

`npm run dev` stages a data package first. With no arguments it prefers the real
export at `../artifacts/phase3/` and falls back to the committed synthetic
fixture. To force the fixture:

```bash
IMPACT_DATA_DIR=docs/fixtures/phase3 IMPACT_ALLOW_FIXTURE=1 npm run dev
```

A fixture build is marked **DEMO DATA** on every page and a production build
refuses to use one unless `IMPACT_ALLOW_FIXTURE=1` is set explicitly.

## Scripts

| Script | What it does |
|---|---|
| `npm run data` | stage `artifacts/phase3` → `web/.data`, verifying every sha256 against the manifest |
| `npm run dev` | stage, then Next dev server |
| `npm run build` | stage, then static export to `web/out` |
| `npm start` | serve `web/out` locally (`http://localhost:3000`) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest component tests |
| `npm run test:e2e` | Playwright; builds and serves `out/` itself |

## Refreshing the data

```bash
make p2-export                                        # regenerate artifacts/phase3
cd web && npm run build                               # rebuild against it
# or, build and publish in one step:
scripts/deploy-dashboard.sh "approved by <name> on <date>"
```

No code change is needed to switch packages — if one ever is, the UI has coupled
to fixture-specific data, which is a bug.

## Environment variables

Names only; none of these hold a secret and none is required for the site to work.

| Name | Purpose |
|---|---|
| `IMPACT_DATA_DIR` | which Phase 3 package to build against |
| `IMPACT_ALLOW_FIXTURE` | `1` permits a synthetic package in a production build |
| `IMPACT_PUBLISH_APPROVAL` | free-text record of a human publication sign-off |
| `NEXT_PUBLIC_BASE_PATH` | sub-path the site is served from (`/Smart-SWE-Impact-Dashboard` on Pages, empty at a domain root) |

## Troubleshooting

**`PHASE 2 DATA CONTRACT VIOLATION` during build.** Working as intended — the
build refuses a package it cannot render honestly. The message names the file and
field. Fix it in Phase 2; do not widen the schema to make it pass.

**`the package does not match its manifest`.** A partially copied or
half-written export. Re-run `make p2-export`.

**Every route serves the overview page.** You ran `serve` in SPA mode. Use
`npm start`, not `serve out --single`.

**Assets 404 after deploying.** `NEXT_PUBLIC_BASE_PATH` did not match where the
site is served from. It is compiled into asset URLs, so the site must be rebuilt
after changing it.

**Build runs out of memory.** The real export is ~190 MB of JSON.
`NODE_OPTIONS=--max-old-space-size=6144` is set by the deploy script; set it by
hand if you invoke `next build` directly.

## Documentation

- [`docs/UI_ARCHITECTURE.md`](../docs/UI_ARCHITECTURE.md)
- [`docs/DATA_INTEGRATION.md`](../docs/DATA_INTEGRATION.md)
- [`docs/DEPLOYMENT_RUNBOOK.md`](../docs/DEPLOYMENT_RUNBOOK.md)
- [`docs/ACCESSIBILITY.md`](../docs/ACCESSIBILITY.md)
- [`docs/PERFORMANCE.md`](../docs/PERFORMANCE.md)
- [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)
- [`docs/PHASE_3_CONTRACT.md`](../docs/PHASE_3_CONTRACT.md) — the data contract this UI is built against
