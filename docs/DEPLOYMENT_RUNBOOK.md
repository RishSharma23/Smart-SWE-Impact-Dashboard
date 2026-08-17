# Deployment runbook — the Phase 3 dashboard

Everything here is reproducible from a clean checkout plus one Phase 2 export.

- **Production URL** — <https://rishsharma23.github.io/Smart-SWE-Impact-Dashboard/>
- **Provider** — GitHub Pages, free tier, no payment method attached
- **Project** — `RishSharma23/Smart-SWE-Impact-Dashboard`, Pages source: branch `gh-pages`, folder `/`
- **Secrets required** — none. Nothing in this deployment uses a token beyond the
  `GITHUB_TOKEN` that Actions issues to itself for CI.

---

## 1. Why the site is built locally and not in CI

The Phase 2 export (`artifacts/phase3/`) is about **190 MB of generated JSON** —
`claims.json` alone is 81 MB and `episodes.json` is 86 MB. It is a build *input*,
not source, so it is `.gitignore`d. GitHub Actions therefore has nothing to build
the production site from.

So the split is:

| What | Where it runs | Data it uses |
|---|---|---|
| Typecheck, unit tests, production build, E2E | GitHub Actions (`.github/workflows/ci.yml`) | the committed synthetic fixture, `docs/fixtures/phase3/` |
| The production site | this machine, `scripts/deploy-dashboard.sh` | the real export, `artifacts/phase3/` |

This is not a compromise on correctness. The data contract is enforced *by the
build itself* (`web/scripts/build-data.mjs` + `web/src/lib/data.ts`), so a CI
build against the fixture still exercises every schema check, sha256 verification
and referential-integrity rule. What CI cannot check is whether the real numbers
are right — that is what the manual verification in §6 is for.

If you would rather CI built production too, the fix is to add a pruning step
that writes only the claims and episodes the rendered pages reference; that would
bring the package to a committable size. It is listed as recommended next work in
`PHASE_3_HANDOVER.md`.

---

## 2. First-time setup (already done — recorded for reproducibility)

```bash
# 1. Pages was enabled against the gh-pages branch:
gh api -X POST repos/RishSharma23/Smart-SWE-Impact-Dashboard/pages \
  -f 'source[branch]=gh-pages' -f 'source[path]=/'

# 2. Nothing else. No account creation, no OAuth, no payment method,
#    no environment variables, no deploy keys.
```

Permissions granted: the `gh` CLI's existing `repo` + `workflow` scopes on the
owner's own account. No third-party app was authorised.

---

## 3. Deploy

```bash
# From the repository root, with a Phase 2 export present:
scripts/deploy-dashboard.sh "Reviewed and approved by <name> on <date>: <what you checked>"
```

That script:

1. refuses to start if `artifacts/phase3/dashboard_manifest.json` is missing;
2. verifies every file in the package against its manifest `sha256` (a partial
   copy fails the build rather than deploying a half-run);
3. builds a static export with `NEXT_PUBLIC_BASE_PATH=/Smart-SWE-Impact-Dashboard`;
4. writes `.nojekyll` so Pages serves `_next/`;
5. refuses to publish if any file exceeds GitHub's 100 MB limit;
6. force-pushes the output as a **single orphan commit** on `gh-pages`.

The orphan commit matters: the site is ~280 MB of HTML and a new snapshot is
produced on every Phase 2 run. Accumulating that as history would bloat the
repository without bound, so each deploy replaces the branch outright.

Pages picks the push up automatically; propagation is usually under a minute.

### The approval argument

`IMPACT_PUBLISH_APPROVAL` (or the first positional argument) records a human
sign-off. It does **not** rewrite the export: `publishable` in the manifest stays
exactly as Phase 2 wrote it, and the site reports both — the approval prominently,
and Phase 2's automated verdict plus its outstanding queue items in a disclosure
beneath it. Omit the argument and the site carries the louder
"Provisional — not yet human reviewed" banner instead.

---

## 4. Automatic redeploy when Phase 2 finishes

```bash
# Poll for a changed export and redeploy when one lands:
nohup scripts/watch-phase2.sh > /tmp/phase3-watch.log 2>&1 &

# Or one-shot, e.g. from the end of a Phase 2 make target:
scripts/watch-phase2.sh --once
```

The watcher fingerprints the manifest's full `sha256` set, so a Phase 2 rerun
that produces byte-identical output does not trigger a pointless redeploy. It
waits for two identical reads 20 s apart before building, so it never reads a
half-written export. A failed build leaves the live site untouched.

State lives in `.git/phase3-deployed-fingerprint`. Delete it to force a redeploy.

---

## 5. Rollback

The live site is whatever commit `gh-pages` points at. Because each deploy is an
orphan commit, rolling back means pointing the branch at a previous snapshot.

```bash
# What has been deployed (the reflog survives force-pushes locally):
git reflog show origin/gh-pages

# Roll back to the previous deployment:
git push --force origin <previous-sha>:refs/heads/gh-pages
```

If no local reflog is available — a different machine, say — the reliable
rollback is to rebuild from the export that produced the good site:

```bash
IMPACT_DATA_DIR=/path/to/known-good/phase3 scripts/deploy-dashboard.sh "rollback to <date> export"
```

Every deployment commit message records the export's `generated_at`, the analysed
repository SHA and the page count, so a snapshot can always be identified.

**To take the site down entirely:** `gh api -X DELETE repos/RishSharma23/Smart-SWE-Impact-Dashboard/pages`.

---

## 6. Manual verification before announcing a deployment

Automated tests cannot tell you the data is right. After each production deploy,
check these against the export by hand:

```bash
# The five names and their order, straight from the package:
python3 -c "
import json; r=json.load(open('artifacts/phase3/rankings.json'))
s=[x for x in r['scenarios'] if x['available'] and x['positions']][0]
for p in sorted(s['positions'], key=lambda p: p['position'])[:5]:
    print(p['position'], 'tier', p['tier'], p['login'])
"
```

Then, on the deployed site:

- [ ] the five names and their order match the command above;
- [ ] each thesis sentence on a leader card appears verbatim in `claims.json`;
- [ ] "Why this ranking?" opens and shows concordance/credibility figures;
- [ ] one evidence drawer per leader opens and its GitHub links resolve;
- [ ] the analysed SHA and window in the header match the manifest;
- [ ] the disabled scenarios still show their reason and remedy;
- [ ] the coverage page reports the same validation status as the manifest.

---

## 7. Alternative providers

The Phase 3 brief prefers Cloudflare Pages. It was not used because it needs a
human to complete signup and OAuth, and GitHub Pages met every requirement
autonomously with no secrets. Both alternatives work with the same output:

**Cloudflare Pages** — create a project, then upload the prebuilt directory
(no build step on their side, since the data is local):

```bash
cd web && NEXT_PUBLIC_BASE_PATH= npx wrangler pages deploy out --project-name posthog-impact
```

**Vercel** — Hobby is for personal, non-commercial use; confirm that fits before
choosing it.

```bash
cd web && NEXT_PUBLIC_BASE_PATH= npx vercel deploy --prebuilt out
```

Both serve from the domain root, so **rebuild with an empty
`NEXT_PUBLIC_BASE_PATH`** first — the GitHub Pages build hard-codes
`/Smart-SWE-Impact-Dashboard` into every asset URL.

---

## 8. Security headers

GitHub Pages does not let you set response headers, so a Content-Security-Policy
cannot be served on this provider. What is in place instead:

- the site is fully static: no runtime API, no inline event handlers, no
  `eval`, no third-party scripts, no analytics;
- every external link is `rel="noopener noreferrer external"`;
- external URLs are allow-listed to `github.com` at build time
  (`isSafeUrl` in `web/src/lib/schema.ts`) and the build *fails* on any
  evidence URL pointing elsewhere;
- the only third-party requests at runtime are avatar images from
  `github.com`, which fall back to a neutral monogram when they fail.

If a CSP is required, move to Cloudflare Pages and add `web/out/_headers`:

```
/*
  Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://github.com; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
```

`'unsafe-inline'` for styles is required by Next's inlined critical CSS.
