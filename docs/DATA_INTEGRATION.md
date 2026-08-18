# Data integration

## Contract

`contracts/PHASE_3_CONTRACT.md`, export schema **1.0.0**. The UI reads only
`artifacts/phase3/` and nothing from `data/` or Phase 1.

## Two-stage validation

**Stage 1 — `web/scripts/build-data.mjs`** (before `next build`):

1. resolves the package (`IMPACT_DATA_DIR`, else real, else fixture);
2. rejects an unapproved `manifest_version`;
3. checks all ten required files exist;
4. verifies every declared file's **sha256 and byte length** against the
   manifest — a partial copy fails here rather than deploying half a run;
5. rejects a synthetic package in a production build unless
   `IMPACT_ALLOW_FIXTURE=1`;
6. copies to `web/.data/` and writes `_provenance.json`.

**Stage 2 — `web/src/lib/data.ts`** (inside the build, so a violation fails
`next build`):

1. Zod-parses all ten files;
2. rejects duplicate `claim_id`, `episode_id`, `actor_cluster_id`, `artifact_id`;
3. walks every cross-reference;
4. requires `default_scenario` to exist and at least one available, populated
   scenario;
5. rejects any evidence URL that is not `https://github.com`;
6. builds the slug map, the episode-page priority list and the claim index.

Failures raise `PHASE 2 DATA CONTRACT VIOLATION` naming the file and field. The
build never falls back to mock data.

## Fatal vs. reported

Not every defect should stop a deployment. The split:

| Defect | Treatment | Why |
|---|---|---|
| unknown `claim_id` | **fatal** | prose would silently vanish |
| unknown `episode_id` in an engineer's list | **fatal** | a link would 404 |
| duplicate id of any kind | **fatal** | joins become ambiguous |
| non-github.com evidence URL | **fatal** | the UI would refuse to link it, hiding evidence |
| unapproved schema version | **fatal** | field meanings may have changed |
| participant → unknown `actor_cluster_id` | **reported** | the UI already renders an unlinked contributor correctly |

Reported defects appear on the **coverage page** under "Defects found in this
export" and are printed during the build. Nothing is swallowed.

## Deviations from the written contract in the current export

Found while integrating the real package. Each is handled without distorting
meaning; all three should be reconciled in Phase 2 or in the contract text.

1. **`claim.confidence` carries corroboration states.** Documented as
   `high|medium|low|null`; `episode_narrative` claims also carry `corroborated`
   and `merged_only`. The schema accepts any string and the UI renders the value
   given. Recolouring `merged_only` as "low confidence" would have been a
   meaning change, so it was not done.

2. **`interval` is `[null, null]` for unassessable dimensions.** Documented as
   `[number, number]`. `intervalOf()` collapses the null form to absent, so the
   UI never renders half an interval or reads a null bound as zero.

3. **Five episode participants reference actors absent from `engineers.json`**
   (all `git/email/<hash>` — commit identities never clustered to a GitHub
   account). Reported on the coverage page; those contributors render without a
   profile link.

## Package size and what it means for hosting

| File | Size | Rows |
|---|---|---|
| `episodes.json` | 86 MB | 9,515 |
| `claims.json` | 81 MB | 122,448 |
| `engineers.json` | 3.1 MB | 352 |
| `indexes.json` | 2.4 MB | — |
| `rankings.json` | 960 KB | 8 scenarios |
| everything else | < 1 MB | — |

The package is a build input, never served. Nothing raw reaches the browser: no
Parquet, no API responses, no emails, no prompts, no secrets. Each page carries
only the slice `viewmodel.ts` selected for it.

Because it is ~190 MB it is **not committed** (`.gitignore`). CI therefore builds
against `docs/fixtures/phase3/` and production is built where the data lives.
See `docs/DEPLOYMENT_RUNBOOK.md` §1.

## Provenance on screen

Displayed on every page: analysed head SHA (linked to the commit), analysis
window, generated timestamp, methodology version. The footer adds export schema
version, package path and size, validation status, `publishable`, safety-scan
status, whether the data is real or fixture, and all manifest counts.
