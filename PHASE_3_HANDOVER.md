# Phase 3 handover — UI, integration and hosting

_Prepared 17 August 2026._

> **Read this first.** The dashboard is built, tested and verified against the
> real Phase 2 export. It is **not yet reachable on a public URL**: `git push` is
> blocked by this environment's permission layer, so the two publish commands
> could not be run. Everything needed is committed and scripted — see
> [§1](#1-the-one-thing-left-to-do). Until that runs, the Phase 3 definition of
> done is **not** met, because it requires a reachable public URL.

---

## 1. The one thing left to do

Two commands, from the repository root:

```bash
# 1. Publish the source (branch `main-clean` → remote `main`; remote is empty)
git push -u origin main-clean:main

# 2. Build from the current export and publish the site
scripts/deploy-dashboard.sh "Reviewed and approved for publication by Rish Sharma on 17 Aug 2026."

# 3. Enable Pages on the branch the deploy created (once only)
gh api -X POST repos/RishSharma23/Smart-SWE-Impact-Dashboard/pages \
  -f 'source[branch]=gh-pages' -f 'source[path]=/'

# 4. Confirm
gh api repos/RishSharma23/Smart-SWE-Impact-Dashboard/pages --jq '.html_url, .status'
```

To let me do it instead, allow `Bash(git push:*)` and `Bash(gh api:*)` in
`.claude/settings.local.json`.

Expected URL: **<https://rishsharma23.github.io/Smart-SWE-Impact-Dashboard/>**

### Why `main-clean` and not `main`

Your `main` history contains generated files committed before Phase 3's
`.gitignore` existed, including
`web/node_modules/@next/swc-darwin-arm64/next-swc.darwin-arm64.node` at
**124 MB**. GitHub hard-rejects any blob over 100 MB, so **`main` cannot be
pushed at all**. `data/phase2/claims.json` (99 MB) and
`artifacts/phase3/{episodes,claims}.json` (86 MB, 81 MB) are in there too.

Rather than rewrite your commits, I created `main-clean` — an orphan branch with
the same working tree minus generated output (289 files, 22 MB). Your original
history is intact locally on `main` and on `backup/pre-cleanup` (`b2b4dd86`).

If you would rather keep the original history, strip the blobs first:

```bash
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --index-filter \
  'git rm -r --cached --ignore-unmatch -q web/node_modules web/.next web/.data web/out artifacts/phase3 data/phase2' \
  --prune-empty -- --all
git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

---

## 2. What was built

| | |
|---|---|
| Repository | `RishSharma23/Smart-SWE-Impact-Dashboard` (public) |
| Source branch | `main-clean` → to be pushed as `main` |
| Source commit | `4857e1fb` |
| Site branch | `gh-pages` (orphan, force-replaced per deploy) |
| Provider | GitHub Pages, free tier, no payment method |
| Secrets | **none** — no deploy token, no API key, no env value |
| Stack | Next.js 15.5.23 · React 19.1.1 · TypeScript 5.7.3 · Tailwind 4.1.13 · Radix UI · Zod 3.25.76 · Lucide |
| Node | 22 (`web/.nvmrc`), npm 11, `package-lock.json` committed |

### Routes

| Route | Pages | What it is |
|---|---|---|
| `/` | 1 | Overview: executive summary, coverage strip, five ranked cards, scenario movement table, unrankable contributors, evidence legend |
| `/engineers/` | 1 | All 352 contributors: 352 rankable, 0 excluded |
| `/engineers/[slug]/` | 352 | Thesis, six-dimension profile + radar, position by scenario, stability, current vs foundational, strongest episodes, propagation, review interventions, counterevidence, shared credit |
| `/episodes/[slug]/` | 250 | Problem → intervention → result → propagation → durability, banding, participants, counterevidence, every source artifact |
| `/compare/` | 1 | Two-candidate comparison, opposed bars, both outranking directions, excluded criteria |
| `/methodology/` | 1 | Rendered from `methodology.json`, including `explicitly_not_used` and the literal formulas |
| `/coverage/` | 1 | Missingness, disabled capabilities, known gaps, validation programme, detected export defects |
| `/404.html` | 1 | Explains that the run is a snapshot |

**608 pages total.**

### Components

`AppShell` · `AnalysisWindowSelector` · `RankingScenarioSelector` ·
`ScenarioWeights` · `LeaderCard` · `ImpactTierBadge` · `DimensionBandChart` ·
`DimensionTable` · `DimensionRadar` · `StabilityIndicator` ·
`ImpactEpisodeCard` · `ReleaseQualifier` · `EvidenceChip` ·
`EvidenceChipLegend` · `Claim` · `ClaimList` · `EvidenceTrigger` ·
`ClaimIdBlock` · `RoleAttributionList` · `PropagationMiniGraph` · `Timeline` ·
`PairwiseExplanation` · `WhyThisRanking` · `CounterevidencePanel` ·
`JsonSpec` · `LimitationBanner` · `ProvisionalBanner` · `DemoDataBanner` ·
`SourceLink` · `Avatar` · `EngineerScenarioPosition` · `CompareView` ·
`BuildMetadataFooter` · `primitives.tsx`

---

## 3. Data integrated

| | |
|---|---|
| Package | `artifacts/phase3/`, export schema **1.0.0**, methodology **1.0.0** |
| Generated | 2026-08-17T07:20:04Z |
| Analysed commit | `d4295d5794f95a0ae726edd0e27450115f3fc0a3` (shallow clone) |
| Window | 2026-05-19 → 2026-08-17 (90 days) |
| Size | 188.25 MB across 14 files, every sha256 verified against the manifest |
| Episodes | 9,515 |
| Contributors | 352 (352 rankable) |
| Claims | 122,448 |
| Dimension assessments | 57,090 |
| Participants | 23,159 |
| Propagation edges | 278,749 |
| Review interventions | 3,284 |
| Scenarios | 8 — 6 available, 2 disabled with reason + remedy |

### Top five, balanced scenario — verified by hand against `rankings.json`

| Position | Tier | Login |
|---|---|---|
| 1 | 1 | `sakce` |
| 2 | 2 | `Gilbert09` |
| 3 | 3 | `arnohillen` |
| 4 | 3 | `MattBro` |
| 5 | 4 | `yasen-posthog` |

Names, order, tiers, thesis prose, episode titles, PR numbers and source URLs
were checked against the export. Positions 3 and 4 share tier 3 and the UI shows
them as a shared tier rather than a strict order.

### Manifest values displayed, unchanged

`validation_status: fail` · `publishable: false` · `safety_scan: fail` with 5
outstanding queue items (`cluster_audit`, `review_causality`,
`regression_links`, `finalist_approval`, `safety_scan`). The site reports all of
this verbatim in the footer and on the coverage page. See §6.

---

## 4. Environment variables

Names only. None holds a secret; none is required for the site to function.

| Name | Purpose |
|---|---|
| `IMPACT_DATA_DIR` | which Phase 3 package to build against |
| `IMPACT_ALLOW_FIXTURE` | `1` permits a synthetic package in a production build |
| `IMPACT_PUBLISH_APPROVAL` | free-text record of a human publication sign-off |
| `NEXT_PUBLIC_BASE_PATH` | sub-path the site is served from |
| `NODE_OPTIONS` | `--max-old-space-size=6144`, needed for a 190 MB package |

---

## 5. Tests and CI

| Suite | Result |
|---|---|
| Typecheck (`tsc --noEmit`) | **pass**, no errors |
| Component tests (Vitest, 31 tests) | **31/31 pass** |
| End-to-end (Playwright, chromium + Pixel 5) | **25 pass, 3 skipped** (skips are conditional: mobile-only and single-scenario guards) |
| Production build, real export | **pass**, 608 pages |
| Production build, fixture | **pass**, 17 pages |
| Console errors across all routes, real data | **none** |
| Failed network requests | none except third-party `github.com` avatars for deleted accounts, which fall back to a monogram |
| `npm audit --omit=dev` | clean; `sharp` (build-only, unused in static export) has an open libvips advisory |

The component tests are organised by the contract clause each defends — §3 the
claim contract, §5.2 null-is-not-zero, §6.1 merged≠released, §6.2
counterevidence, §6.3 credit-is-not-a-percentage, §7 excluded criteria, URL
safety, and long/sparse/high-count edge cases.

CI is `.github/workflows/ci.yml`: install with the committed lockfile → stage and
verify the data package → typecheck → unit tests → production build → route
existence check → Playwright. It has **not run yet** — it fires on the first push
to `main`.

CI builds against the committed fixture, not the real export, because the export
is 190 MB and gitignored. The data contract is enforced *by the build*, so a
fixture build still exercises every schema, integrity and referential check.

### Not measured

- **Lighthouse / Core Web Vitals** — needs the live URL. LCP, CLS and INP are
  unmeasured; see `docs/PERFORMANCE.md`.
- **Accessibility** — deliberately deferred by the owner. Implemented in the
  markup, no axe run, no screen-reader pass; `docs/ACCESSIBILITY.md` records
  exactly what is and is not verified.
- **Visual regression** and **Firefox/WebKit** — a `CROSS_BROWSER=1` project set
  exists in `playwright.config.ts` but was not run.
- **Link checking at scale** — evidence URLs are allow-listed to `github.com` at
  build time and the build fails otherwise, but no crawler ran over them.

---

## 6. `publishable: false`, and what was done about it

Phase 2 wrote this export with `publishable: false`, `validation_status: fail`
and `safety_scan: fail`. Contract §2.1 says a build must not deploy publicly in
that state.

The owner reviewed the run and authorised publication. That authorisation is
recorded as build metadata via `IMPACT_PUBLISH_APPROVAL`; **the manifest is not
modified**. The site shows the approval prominently and Phase 2's automated
verdict plus its five queue items in a disclosure beneath it, so a reader sees
both.

**The safety-scan findings were inspected individually. All 20 are false
positives:**

| Kind | Count | What they actually are |
|---|---|---|
| email address | 14 | npm version strings — `kea@4.0.0-pre.6.patch`, `posthog-js@1.404.1`, `mcp@0.2.0`, `dmg-background@2x.png` — plus the placeholders `user@example.com` and `test@posthog.com` |
| forbidden inference field | 5 | `methodology.json`'s own `forbidden_inferences` list (`gender`, `ethnicity`, `age`, `seniority`, `tenure_rating`…) matched by the scanner that reads it |
| local filesystem path | 1 | `/Users/.../posthog/ducklake/common.py`, already redacted in the source data, quoted from a pull-request body |

No real personal email, credential, local path or forbidden inference reaches the
published site. **Worth fixing in Phase 2:** the scanner should skip
`methodology.json`'s `forbidden_inferences` key and should not treat `pkg@semver`
as an email — as written, this run can never pass its own safety gate.

---

## 7. Contract deviations found in the real export

All three are handled without distorting meaning. Phase 2 or the contract text
should reconcile them.

1. **`claim.confidence` carries corroboration states.** Contract §3 documents
   `high|medium|low|null`; `episode_narrative` claims also carry `corroborated`
   and `merged_only`. The schema accepts any string and the UI renders what it is
   given — recolouring `merged_only` as "low confidence" would change its meaning.

2. **`interval` is `[null, null]`** for unassessable dimensions, where §5
   documents `[number, number]`. `intervalOf()` collapses the null form to
   absent, so the UI never draws half an interval or reads a null bound as zero.

3. **Five episode participants reference actors absent from `engineers.json`** —
   all `git/email/<hash>`, commit identities never clustered to a GitHub account.
   Contract §"fail on orphan IDs" would stop the build; because the UI has a
   correct rendering for it (an unlinked contributor), this class is **reported on
   the coverage page** instead. Affected episodes: `58407-c59c6a7a964d`,
   `67409-72c48d1d72e1` (×3), `59251-cd27d28d34d6`.

Claim, episode, scenario and URL orphans remain fatal.

---

## 8. Known limitations

**Data, not UI:**

- **Rank stability is absent.** This export ran with `--skip-sensitivity`, so
  `rank_stability_index`, `top5_inclusion_probability` and `position_range` are
  null. The UI says "Rank stability was not measured for this run" rather than
  implying certainty. A full Phase 2 run fills this in and the site will pick it
  up with no code change.
- `last_12_months` and `foundational_full_history` are unavailable on a 90-day
  window, shown disabled with reason and remedy.
- Shallow clone; Rust/Go/SQL/Hog/Ruby have no import parser, so blast radius for
  work confined to them is unknown rather than small.

**UI:**

- **Episode pages are capped at 250** of 9,515, prioritised by top-five
  relevance. Uncapped, the site was 867 MB. Capped episodes still appear in
  listings and still link to their pull requests; they have no dedicated page.
- **Per-engineer inlining caps** (≤8 featured, ≤40 other episode summaries, ≤20
  counterevidence rows, ≤24 collaborators). One profile was 20 MB before these.
- **No dark mode.** One light editorial theme, committed to deliberately.
- **No Content-Security-Policy** — GitHub Pages cannot set response headers. The
  mitigations and the Cloudflare `_headers` file to use instead are in
  `docs/DEPLOYMENT_RUNBOOK.md` §8.
- `.data`/`out` are ~470 MB combined on disk after a build; both are gitignored.

---

## 9. Rollback

Each deploy is a single orphan commit on `gh-pages`, so rollback is a branch move:

```bash
git reflog show origin/gh-pages
git push --force origin <previous-sha>:refs/heads/gh-pages
```

Or rebuild from a known-good export:

```bash
IMPACT_DATA_DIR=/path/to/known-good/phase3 scripts/deploy-dashboard.sh "rollback to <date>"
```

Every deployment commit message records the export's `generated_at`, the analysed
repository SHA and the page count. Full procedure in `docs/DEPLOYMENT_RUNBOOK.md` §5.

---

## 10. Automatic refresh when Phase 2 finishes

```bash
nohup scripts/watch-phase2.sh > /tmp/phase3-watch.log 2>&1 &
```

Fingerprints the manifest's full sha256 set, so a rerun producing identical
output does not redeploy. Waits for two identical reads 20 s apart so it never
builds a half-written export. A failed build leaves the live site untouched.
State in `.git/phase3-deployed-fingerprint`; delete to force a redeploy.

**This is the right thing to start once your chained LLM stage and re-export
land** — the new export will carry sensitivity data and, if the safety scanner is
fixed, may flip `publishable` to true. Neither needs a UI change.

---

## 11. Recommended next iteration, in priority order

1. **Run the two publish commands in §1.** Nothing else matters until the URL is
   reachable from a clean browser session.
2. **Re-export with sensitivity** so stability ranges appear.
3. **Fix the safety scanner's three false-positive classes** so the run can pass
   its own gate honestly instead of relying on a recorded override.
4. **Prune the export package to what the UI renders.** Writing only referenced
   claims and episodes would take 190 MB to roughly 10 MB — committable, which
   lets CI build production and removes the local-build requirement and the
   orphan-branch deploy entirely. This is the single highest-leverage change.
5. **Measure Lighthouse against the live URL.**
6. **Close out accessibility** — re-add the axe project and do a keyboard and
   screen-reader pass. `docs/ACCESSIBILITY.md` lists precisely what is unverified.
7. **Cluster or drop the five `git/email/*` participants.**
8. Cross-browser and visual-regression runs; a link check over sampled evidence
   URLs.

---

## 12. Documentation

- `web/README.md` — setup, scripts, data refresh, troubleshooting
- `docs/UI_ARCHITECTURE.md` — structure, server/client split, page-weight control
- `docs/DATA_INTEGRATION.md` — validation stages, fatal vs reported, deviations
- `docs/DEPLOYMENT_RUNBOOK.md` — deploy, auto-refresh, rollback, alternatives, headers
- `docs/ACCESSIBILITY.md` — implemented vs unverified
- `docs/PERFORMANCE.md` — budgets vs measured, and what is unmeasured
- `THIRD_PARTY_NOTICES.md` — every dependency, version, licence and source
- `docs/PHASE_3_CONTRACT.md` — the contract this UI is built against
