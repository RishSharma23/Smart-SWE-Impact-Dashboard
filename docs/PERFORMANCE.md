# Performance

Measured on the production build of the real Phase 2 export
(9,515 episodes, 122,448 claims), `NEXT_PUBLIC_BASE_PATH=/Smart-SWE-Impact-Dashboard`.

## Budget vs. measured

| Budget (Phase 3 brief) | Target | Measured | |
|---|---|---|---|
| Initial compressed JS | < 200 KB | **~103 KB gzipped** shared + 0.2–7 KB per route | met |
| Static-first rendering, no runtime API | required | fully static export; zero fetches at runtime | met |
| Largest Contentful Paint (mid-tier mobile) | < 2.5 s | **not measured** | unmeasured |
| Cumulative Layout Shift | < 0.1 | **not measured**; images have explicit width/height, fonts are system fonts, so the structural causes are absent | unmeasured |
| Interaction to Next Paint | < 200 ms | **not measured**; scenario switching is a local re-render with all data already in the page | unmeasured |
| Lazy-load heavy chart libraries | required | **no chart library exists** — charts are hand-drawn CSS/SVG | met differently |
| System or self-hosted fonts | required | platform UI stack only; zero font requests | met |
| Images optimised with dimensions | required | avatars are `loading="lazy"` `decoding="async"` with explicit `width`/`height` | met |
| Cache immutable assets with content hashes | required | Next content-hashes everything under `_next/static/` | met |

Lighthouse and Core Web Vitals were **not run**. They need a deployed URL plus a
throttled run; the URL exists now, so this is the first thing to measure next.
Nothing in this file should be read as a Lighthouse result.

## Bundle

```
First Load JS shared by all            103 kB
  chunks/4bd1b696…                      54.2 kB   react + react-dom
  chunks/255…                           46.5 kB   next runtime + radix + lucide
  other shared chunks                    1.9 kB

Route                                   Route JS
/                                        6.9 kB
/compare                                 3.8 kB
/engineers/[slug]                        3.2 kB
/coverage, /engineers                    0.2 kB
/methodology, /episodes/[slug]           0.2 kB
```

The 103 KB shared figure is Next's own report (uncompressed per-chunk, gzipped
total). Overview HTML gzips to ~57 KB.

## Page weight, and the caps that produce it

608 pages, 282 MB uncompressed on disk; ~50–60 KB gzipped over the wire per page.
The floor is ~184 KB uncompressed per page: 81 KB of HTML and 100 KB of React
Server Component payload for the shared shell. That duplication is inherent to
static export with a data-carrying shell.

Two caps keep it bounded, both deliberate and both documented in
`docs/UI_ARCHITECTURE.md`:

- **250 episode pages**, prioritised: top five of every available scenario first,
  then remaining ranked engineers.
- **Per-engineer inlining caps** — ≤8 featured / ≤6 current / ≤6 foundational /
  ≤40 other episode summaries, ≤8 thesis claims, ≤20 review interventions,
  ≤20 counterevidence rows, ≤24 collaborators.

Before these existed, one prolific contributor's profile inlined ~1,800 claim
objects and measured **20 MB**; the whole site was 867 MB. The caps cut it to
896 KB worst case and 282 MB total, with no loss of evidence access — capped
items still appear in listings and still link to github.com.

## Build

| | |
|---|---|
| Staging + sha256 verification of 188 MB | ~2 s |
| `next build` (608 pages) | ~105 s |
| Peak Node heap | ~4 GB (`--max-old-space-size=6144` required) |

## Reliability

Verified on the fixture build with Playwright and a scripted console-error watch
across all routes:

- no uncaught console errors, no page errors, no hydration warnings;
- no failed network requests except third-party `github.com` avatars for accounts
  that no longer exist, which fall back to a neutral monogram;
- no horizontal page scroll at 360 px on any top-level route;
- direct deep-link refresh and 404 behaviour both correct.

## Where to improve first

1. **Run Lighthouse against the live URL** — LCP/CLS/INP are the three unmeasured
   budgets.
2. **Prune the data package** so CI can build production. Writing only the claims
   and episodes the rendered pages reference would take the package from 190 MB to
   an order of ~10 MB, which is committable, which removes the local-build
   requirement entirely.
3. **Trim the shell payload.** The 100 KB per-page RSC payload is the single
   largest lever on total size; most of it is the header and footer element tree.
