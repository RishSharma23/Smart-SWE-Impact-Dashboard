# Accessibility

**Status: implemented in the markup, not yet audited.** Accessibility
compatibility was explicitly deferred by the project owner during Phase 3, so
there is no automated gate in CI and no manual screen-reader pass has been done.
This file records honestly what is in place and what is unverified, so the audit
has a starting point rather than a blank page.

## In place

Built in as the components were written:

- **Semantics** — one `<h1>` per page, ordered headings, `<header>`/`<main>`/
  `<footer>` landmarks, `<nav aria-label>` on both navigations, every content
  block in a `<section aria-labelledby>`.
- **Skip link** — first focusable element on every page, targets `#main`.
- **Focus** — a global `:focus-visible` outline that is never removed; the
  evidence and "why this ranking?" panels are Radix dialogs, so focus is trapped
  and returned to the trigger on close.
- **Charts** — every visualization carries a `role="img"` with a full textual
  `aria-label`, *and* a table or `<dl>` with the same numbers. The dimension
  chart's table includes the aggregation trace.
- **No colour-only encoding** — evidence chips pair each colour with a distinct
  shape (filled square, filled circle, dashed ring, cross, hatch) and a text
  label. Band values always appear as a word ("material") next to the number.
  Unassessable dimensions are hatched *and* labelled "not assessable".
- **Controls** — the scenario selector is a labelled `radiogroup`; the comparison
  pickers are `<select>` elements with `<label>`; icon-only buttons carry
  `sr-only` text.
- **Live regions** — scenario changes announce through a `role="status"` region;
  the claim-id copy button announces its result.
- **Tables** — `<caption>`, `scope="col"`/`scope="row"`, and wide tables scroll
  inside a focusable `role="region"` container so the page itself never scrolls
  horizontally.
- **Touch targets** — interactive elements are ≥44 px in at least one axis
  (`min-h-11`, `size-11`).
- **Reduced motion** — `prefers-reduced-motion: reduce` zeroes every transition
  and animation and disables the card lift.
- **Contrast** — the palette was chosen against a 4.5:1 target on the off-white
  ground; foreground tokens (`--color-ink` #14161b, `--color-ink-soft` #3c414d,
  `--color-muted` #5a6071) all exceed it on `--color-ground` #f3f2ec.

## Verified only incidentally

- **No horizontal page scroll at 360 px** — asserted by a Playwright test across
  all five top-level routes. This is the one accessibility-adjacent property with
  automated coverage.
- **Mobile navigation opens and reaches every route** — Playwright, Pixel 5
  viewport.
- **Keyboard reachability of the skip link, scenario radios and evidence
  drawer** — was covered by a test that was removed when the gate was deferred;
  the markup it exercised is unchanged.

## Not verified

Do not claim any of these without doing the work:

- **No axe-core run.** `@axe-core/playwright` is installed and a scaffold exists
  in git history, but no automated scan has been executed, so the
  "zero serious/critical violations" target in the Phase 3 brief is **unmet and
  unmeasured**.
- **No screen-reader pass.** VoiceOver/NVDA behaviour on the evidence drawer, the
  scenario live region and the band charts is unknown.
- **No contrast measurement.** Tokens were chosen against a target, not
  instrumented. The yellow accent (`#f0b400`) is used as a *background* with dark
  ink on top for this reason, but this has not been measured.
- **Colour-blind review** of the six dimension hues has not been done. The shape
  pairing on chips mitigates this; the dimension colour swatches rely on the
  adjacent text label.
- **Zoom to 200% / 400%** reflow untested.

## To close this out

```bash
cd web
npx playwright test          # re-add an axe project first
```

Then a manual keyboard pass on: overview → leader card → evidence drawer →
engineer detail → episode detail → comparison, plus one screen-reader pass over
the overview and one engineer page.
