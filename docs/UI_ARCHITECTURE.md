# UI architecture

## Shape of the thing

A Next.js 15 App Router project compiled to a **fully static export**
(`output: 'export'`). Every page is HTML on disk before anyone visits. There is
no server, no API route, no database and no model call at render time.

```
web/
  scripts/build-data.mjs     stage + verify the Phase 2 package  →  web/.data
  src/lib/
    schema.ts                Zod schemas + the URL allow-list
    data.ts                  server-only loader: parse, validate, index, slug
    viewmodel.ts             server-only: decides what each page inlines
    ui.ts                    vocabulary, formatting, band names, dimension colours
    scenario.ts              scenario metadata shared by server and client
  src/app/
    layout.tsx               shell, scenario provider, provenance footer
    page.tsx                 overview / top five
    engineers/page.tsx       every contributor, ranked and unranked
    engineers/[slug]/        engineer detail
    episodes/[slug]/         episode detail
    compare/page.tsx         two-candidate comparison
    methodology/page.tsx     renders methodology.json
    coverage/page.tsx        renders coverage.json + detected export defects
    not-found.tsx            404
  src/components/            the component inventory (below)
```

## The one rule the architecture is built around

Every human-readable sentence is a `claim_id` lookup. There is exactly one
component that renders claim prose — `Claim` — and it always renders the
evidence trigger alongside the text. A page cannot show a sentence about a person
without also showing the way to check it, because the two are the same component.

`<Claim claim={null} />` renders **nothing**. That is deliberate: a rejected
claim leaves a gap, not a placeholder.

## Server / client split

Almost everything is a server component, evaluated once at build time. Client
components exist only where there is genuine interaction:

| Client component | Why it needs to be one |
|---|---|
| `ScenarioProvider` | holds the selected scenario, persists it, announces changes |
| `AppShell` | active-route highlighting, mobile nav |
| `Selectors` | scenario / window controls |
| `Claim` + evidence drawer | focus-trapped dialog, clipboard |
| `LeaderCard`, `OverviewLeaders` | re-render on scenario change |
| `CompareView` | two-select comparison state |
| `PairwiseExplanation` | the "why this ranking?" dialog |
| `EngineerScenarioPosition` | highlights the selected scenario's row |
| `Avatar` | `onError` fallback to a monogram |

The scenario is client state rather than a route parameter because the whole
dashboard is a static export and every scenario's data is already in the page —
switching is instant and needs no navigation.

## How page weight is controlled

The real export is ~190 MB across 9,515 episodes and 122,448 claims. A static
site cannot inline that, so `viewmodel.ts` is the single place that decides what
each page carries, with explicit caps:

| Page | What it inlines |
|---|---|
| Overview | top five for every scenario + all pairwise material + coverage counts |
| Engineer detail | ≤8 featured, ≤6 current, ≤6 foundational, ≤40 other episode *summaries*, ≤8 thesis claims, ≤20 review interventions, ≤20 counterevidence, ≤24 collaborators, ≤6 propagation rows |
| Episode detail | the whole episode: all dimensions, participants, artifacts, analytics |

Episode detail pages are generated for **250 episodes**, chosen in priority
order: the top five of every available scenario first, then the rest of the
ranked engineers. Episodes past that cap still appear in listings and still link
to their pull requests; they just have no dedicated page. Without these caps one
prolific contributor's profile measured 20 MB.

The caps are constants at the top of `viewmodel.ts` and `data.ts`. Raising them
raises site size roughly linearly.

## Component inventory

`AppShell` · `AnalysisWindowSelector` · `RankingScenarioSelector` ·
`ScenarioWeights` · `LeaderCard` · `ImpactTierBadge` · `DimensionBandChart` (+
`DimensionTable`, `DimensionRadar`) · `StabilityIndicator` ·
`ImpactEpisodeCard` (+ `ReleaseQualifier`) · `EvidenceChip` (+
`EvidenceChipLegend`) · `Claim`/`ClaimList`/`EvidenceTrigger`/`ClaimIdBlock` ·
`RoleAttributionList` · `PropagationMiniGraph` · `Timeline` ·
`PairwiseExplanation` (+ `WhyThisRanking`) · `CounterevidencePanel` ·
`CoveragePanel` (in `app/coverage`) · `JsonSpec` (methodology/coverage config) ·
`LimitationBanner` / `ProvisionalBanner` / `DemoDataBanner` · `SourceLink` ·
`Avatar` · `EngineerScenarioPosition` · `CompareView` · `BuildMetadataFooter` ·
`primitives.tsx` (`Card`, `Badge`, `Callout`, `KeyValue`, `SectionHeading`,
`TableScroll`, `DataTable`, `Th`, `Td`, `NotObserved`, `EmptyState`)

## Charts

There is no charting library. Three visualizations are hand-drawn:

- **`DimensionBandChart`** — CSS bars on the 0–4 band scale, with the
  interval drawn as a translucent range behind the value. Primary display.
- **`DimensionRadar`** — inline SVG, secondary display only. Unknown spokes are
  drawn as a crossed marker at the axis edge, never pulled to the centre.
- **`PropagationMiniGraph`** — inline SVG reach rings, log-scaled.

Every one has a table or `dl` beside it with the same numbers. A chart library
would have added ~50 KB gzipped to render six bars, and none of them can express
"this axis is not assessable" without being fought.

## Rendering rules encoded in components, not conventions

These are the ones easy to get wrong, and where each is enforced:

| Rule | Where |
|---|---|
| null is not zero | `DimensionBandChart` hatched slot; `intervalOf()`; `OpposedBars` |
| merged ≠ released | `ReleaseQualifier`, always beside `status` |
| credit is a category, never a percentage | `RoleAttributionList` |
| unconfirmed ≠ a regression | `CounterevidencePanel` |
| no composite score | there is no score field in any view model |
| tiers over positions | `ImpactTierBadge` |
| unavailable scenarios are disabled, not hidden | `Selectors` |
| only github.com URLs render as links | `SourceLink` + `isSafeUrl` |

## Styling

Tailwind 4 with a token layer in `globals.css`. One light editorial theme,
committed to deliberately rather than shipping a half-tested dark mode. Motion is
150–250 ms, limited to card lift, a scenario crossfade and a progressive timeline
reveal, all removed under `prefers-reduced-motion`.
