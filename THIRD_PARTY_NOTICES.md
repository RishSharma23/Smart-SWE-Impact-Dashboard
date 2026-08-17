# Third-party notices

Every third-party dependency shipped to the browser or used to build the
dashboard, with its licence. No component was imported from an unmaintained
"dashboard template", and every copied component is owned and tested here.

## Shipped to the browser

| Component | Version | Licence | Source |
|---|---|---|---|
| React | 19.1.1 | MIT | <https://github.com/facebook/react> |
| Next.js | 15.5.23 | MIT | <https://github.com/vercel/next.js> |
| Radix UI — `react-dialog` | 1.1.15 | MIT | <https://github.com/radix-ui/primitives> |
| Radix UI — `react-tabs` | 1.1.13 | MIT | <https://github.com/radix-ui/primitives> |
| Radix UI — `react-accordion` | 1.2.12 | MIT | <https://github.com/radix-ui/primitives> |
| Radix UI — `react-tooltip` | 1.2.8 | MIT | <https://github.com/radix-ui/primitives> |
| Radix UI — `react-select` | 2.2.6 | MIT | <https://github.com/radix-ui/primitives> |
| Radix UI — `react-slot` | 1.2.3 | MIT | <https://github.com/radix-ui/primitives> |
| Lucide icons (`lucide-react`) | 0.544.0 | ISC | <https://github.com/lucide-icons/lucide> |
| `clsx` | 2.1.1 | MIT | <https://github.com/lukeed/clsx> |
| `tailwind-merge` | 2.6.0 | MIT | <https://github.com/dcastil/tailwind-merge> |
| `class-variance-authority` | 0.7.1 | Apache-2.0 | <https://github.com/joe-bell/cva> |

## Build and test only — not shipped

| Component | Version | Licence | Source |
|---|---|---|---|
| TypeScript | 5.7.3 | Apache-2.0 | <https://github.com/microsoft/TypeScript> |
| Tailwind CSS | 4.1.13 | MIT | <https://github.com/tailwindlabs/tailwindcss> |
| Zod | 3.25.76 | MIT | <https://github.com/colinhacks/zod> |
| Vitest | 4.x | MIT | <https://github.com/vitest-dev/vitest> |
| Testing Library (`react`, `jest-dom`, `user-event`) | current | MIT | <https://github.com/testing-library> |
| Playwright | 1.56.x | Apache-2.0 | <https://github.com/microsoft/playwright> |
| `serve` | 14.x | MIT | <https://github.com/vercel/serve> |
| PostCSS | 8.5.26 | MIT | <https://github.com/postcss/postcss> |

## Design system

**Written here, not copied.** The visual layer is hand-authored Tailwind in
`web/src/app/globals.css` and `web/src/components/`. Specifically:

- **shadcn/ui was not copied in.** It is MIT-licensed and the Phase 3 brief
  allows it, but its value is pre-styled Radix wrappers, and the accessible
  primitives this dashboard needs (dialog, disclosure, radio group) are thin
  enough that owning ~40 lines each was clearer than importing a component
  library's conventions. Radix itself — the accessibility engine underneath
  shadcn/ui — *is* used directly, and is credited above.
- **No Uiverse components were used.** The brief permits one or two decorative
  controls from it. None were taken: every control on this dashboard carries
  information (a scenario, a band, an evidence state), and a decorative
  component would have needed rewriting to carry it. Nothing is owed
  attribution as a result.
- **No fonts are downloaded.** Typography uses the platform UI stack
  (`ui-sans-serif`/`system-ui`, `ui-monospace`), so there is no font licence to
  observe and no network request to make.
- **No PostHog brand assets are used.** The palette is a warm off-white ground
  with a yellow accent and six dimension hues, chosen to be *adjacent* to
  PostHog's public look without reproducing any proprietary logo, wordmark or
  brand file. The site mark in the header is three rectangles drawn inline.

## Data

Repository content analysed from <https://github.com/PostHog/posthog> (MIT, with
portions under the PostHog Enterprise licence — episodes touching
enterprise-licensed paths are flagged as such in the UI). Only public GitHub API
and public Git history were read. Avatars are served from `github.com` at render
time and are not redistributed.
