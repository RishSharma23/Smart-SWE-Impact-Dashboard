# Repository Mapping — PostHog conventions and component rules

How this pipeline decides *what part of the product a change touched* and
*which team is accountable for it*, and why those are two different questions.

Everything here was derived from the repository at
`d4295d5794f95a0ae726edd0e27450115f3fc0a3`, not from intuition. The files it
reads are snapshotted with their SHA-256 hashes into
`artifacts/component_rules_snapshot.json`, so a mapping decision can always be
traced back to the exact file content that produced it.

---

## 1. What PostHog's monorepo actually looks like

From `docs/internal/monorepo-layout.md` at the analysed commit:

| Path | Role |
|---|---|
| `posthog/` | Legacy Django monolith — `api/`, `models/`, `hogql/`, `temporal/`, `dags/`, `clickhouse/` |
| `products/<name>/` | **84 product verticals**, each a vertical slice: `backend/` (Django app), `frontend/` (React), `manifest.tsx`, optional `services/`, `packages/`, `mcp/`, `skills/` |
| `ee/` | Enterprise features — **proprietary licence, not MIT** |
| `frontend/` | Main React app — `scenes/`, `lib/`, `queries/`, `toolbar/`, `layout/` |
| `nodejs/` | Node ingestion / plugin-server successor |
| `rust/` | Rust services (capture, feature-flags, …) |
| `common/` | Shared code, explicitly a "holding pen, NOT a destination" |
| `packages/` | Libraries shared by more than one product |
| `services/` | Independent services owned by no single product |
| `tools/`, `cli/`, `bin/`, `devenv/` | Developer/CI tooling, not imported by runtime code |

Boundaries are enforced by `tach.toml` (Python import boundaries) and Turbo
(selective testing). Products deliberately do not import each other's
internals — which is exactly why *crossing a product boundary* is meaningful
evidence rather than noise.

---

## 2. Component resolution — the six-level priority order

Implemented literally in [`src/impact/normalize/components.py`](../src/impact/normalize/components.py).
Every resolved path records which level answered it, in
`pr_files.component_rule_priority`.

| Priority | Source | Derived at run time? | Found at HEAD |
|---|---|---|---|
| **1** | `products/*/manifest.tsx` product manifests | yes | 84 product dirs |
| **2** | Nearest enclosing `owners.yaml` (ownership) | yes | 26 files |
| **2** | Nearest `AGENTS.md` (path-local instructions) | yes | 44 files |
| **3** | `.github/CODEOWNERS` / `CODEOWNERS-soft` | yes | present / **absent** |
| **4** | Conventions in `config/components.yaml` | no — hand-written | 40 rules |
| **5** | Language-aware module graph | yes | 141,389 edges |
| **6** | `unknown` | — | reported, never hidden |

Only priority 4 is hand-written. Levels 1–3 and 5 are read from the analysed
commit so they cannot drift from the repository.

### Component and owner are different dimensions

Priorities 1 and 4 answer *"what part of the product is this?"*.
Priorities 2 and 3 answer *"which team is accountable?"*.

They are resolved separately and stored in separate columns. Collapsing them
would lose the case PostHog cares about most: a file inside a product
directory that is owned by a platform team. `pr_blast_radius` reports
`crosses_component_boundary` and `crosses_ownership_boundary` as independent
signals for the same reason.

---

## 3. Ownership: PostHog uses distributed `owners.yaml`, not CODEOWNERS

This is the single most important mapping finding, and getting it wrong would
mis-attribute most of the repository.

`.github/CODEOWNERS` exists but is deliberately tiny and **argues against its
own use**:

> WE DO NOT USE CODEOWNERS FOR MANAGING ORDINARY RISK. […] Adding entries to
> the codeowners file is an anti-social and anti-posthog-values thing to do.
> It must have extraordinary justification.

It covers only hard review gates — ClickHouse migrations, HogQL, and a handful
of security-sensitive CI workflows. **`.github/CODEOWNERS-soft` does not
exist**; that absence is recorded as `status: missing` in the config snapshot
rather than skipped.

Real ownership lives in **26 nested `owners.yaml` files**, resolved by
PostHog's own `posthog_owners` resolver. Format:

```yaml
version: 1
owners: []                      # directory-level default
teams:
    logs: { slack: '#team-apm' }
rules:
    - match: '/caching/'        # anchored to THIS file's directory
      owners: team-analytics-platform
    - match: '*llm_prompt*.py'  # unanchored: matches at any depth below
      owners: team-ai-observability
    - match: ['/api/proxy.py', '/api/test/test_proxy.py']
      owners: team-ai-gateway
    - match: '/models/llm_prompt.py'
      owners: [team-ai-observability, team-experiments]   # shared ownership
```

The resolver implements PostHog's semantics exactly:

* **nearest enclosing** `owners.yaml` wins (deepest directory first);
* a pattern starting with `/` is **anchored** to that file's directory;
* a bare `x/` or `x` is **unanchored** and matches at any depth beneath it;
* within one file, the **last matching rule wins** (CODEOWNERS-style);
* multiple owners are kept as a list and flagged as shared ownership in
  `mapping_uncertainty`, never silently reduced to one.

Files matched by nothing get `owners = []` and an explicit
`"no owner rule matched this path"` uncertainty entry.

---

## 4. Path classification

`config/generated_files.yaml` labels every path along several axes at once —
a generated test snapshot is genuinely `generated` *and* `snapshot` *and*
`test`, and collapsing that to one label loses information.

**Categories:** lockfile · snapshot · generated · vendor · migration · test ·
docs · config · ci · infrastructure · styling · localization · binary_asset

**Risk surfaces** (evidence markers, never severity scores): public_api ·
schema · migration · auth_privacy · billing · ingestion · data_pipeline ·
deployment · shared_library · feature_flag_surface

Nothing is ever *filtered* by a label. A lockfile-only PR stays in the dataset
with `lockfile_only` in `anomaly_flags`, so a consumer can decide what it means.

### Glob semantics

`fnmatch` is deliberately not used: its `*` crosses `/`, so `posthog/api/**`
would also match `products/x/posthog/api/y`. Globs are compiled to anchored
regexes where `*` stops at `/`, `**/` matches zero or more directories, and a
trailing `/**` also matches the directory itself.

> A bug worth recording: an earlier version normalised paths with
> `lstrip("./")`. `str.lstrip` strips a *character set*, so
> `.github/workflows/ci.yml` silently became `github/workflows/ci.yml` and
> every dot-directory in the repository failed to match its rules. Caught by
> a path-classification test; fixed in `normalize_repo_path`.

---

## 5. Dependency graph

Bounded on purpose — the spec rules out a whole-repository compiler build.

| Language | Method | Coverage at HEAD |
|---|---|---|
| Python | stdlib `ast` | 129,067 statements, **100%** of in-repo imports resolved |
| TypeScript / JavaScript | lexical parse + real `tsconfig` alias maps | 101,281 statements |
| Rust, Go, SQL, Hog, Ruby | **not parsed** | 1,774 files are nodes with no edges |

**In-repo resolution rate: 99.4%** (141,389 of 142,239 resolvable imports).

Two findings that mattered:

1. **PostHog has 110 `tsconfig.json` files**, and the same alias means
   different things in different workspaces — `~/*` is `frontend/src/*` at the
   root but `nodejs/src/*` inside `nodejs/`. Resolving everything against the
   root config left 4,569 imports unresolved. Walking outward from each file to
   its **nearest enclosing tsconfig** cut that to 572.
2. **`pathlib` does not collapse `..`.** `PurePosixPath('a/b/../c')` stays
   literal, so every upward relative import (`../src/filter`) failed to
   resolve — 10,699 of them. Switching to `posixpath.normpath` recovered them
   and took the resolution rate from 58% to 99.4%.

Both are recorded here because they are the kind of silent under-reporting that
would have made blast radius look systematically small.

`unknown` is a first-class reachability band. A Rust-only change has no parsed
edges, and reporting it as `local` would assert something never measured — the
band is downgraded to `unknown` with the reason attached.

---

## 6. Licence areas

| Path prefix | Licence | Handling |
|---|---|---|
| `ee/` | PostHog Enterprise (proprietary) | tagged `license_area`; `touches_enterprise_licensed_code` on the PR; never aggregated as ordinary MIT contribution |
| everything else | MIT | repository default per `LICENSE` |

---

## 7. Feature flags

PostHog's flag registry lives in `frontend/src/lib/constants.tsx` as
`export const FEATURE_FLAGS = { CONST: 'kebab-key', … }` — **388 keys** at the
analysed commit, many annotated with their owning team:

```ts
BILLING_FORECASTING_ISSUES: 'billing-forecasting-issues', // owner: #team-billing, see `Billing.tsx`
```

The registry is parsed into `CONST → key` and `key → owner`, then flags are
extracted at three confidence levels: a `FEATURE_FLAGS.X` constant resolved
through the registry (**strong**), a string literal passed to a known flag API
(**medium**), an unresolved constant (**weak**). Diff-side extraction is
restricted to added/removed lines, which distinguishes *this PR touched the
flag* from *the flag was in the surrounding context*.

---

## 8. Coverage targets

Advisory thresholds in `config/components.yaml`, reported by `make validate`:

| Target | Threshold |
|---|---|
| path classification rate | ≥ 0.95 |
| unknown component rate | ≤ 0.05 |
| in-repo import resolution | ≥ 0.80 (**achieved: 0.994**) |

Actual figures land in `artifacts/quality_report.json` and
[QUALITY_REPORT.md](QUALITY_REPORT.md).
