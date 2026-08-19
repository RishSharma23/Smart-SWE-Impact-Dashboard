# Contributing

Thanks for looking. This is a static, self-hosted engineering-impact dashboard:
a Python pipeline that reads a repository's public record and writes an
evidence-linked export, and a Next.js site that renders it.

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and, for anything involving
tokens or published data, [SECURITY.md](SECURITY.md).

## Setup

```bash
make deps                    # .venv + pinned Python dependencies
cp .env.example .env         # GITHUB_TOKEN, read-only, or run: gh auth login
cd web && npm ci             # the dashboard's dependencies, from the lockfile
```

Python 3.12+, Node 20.11+, git 2.40+. macOS and Linux are supported. On Windows,
use WSL2.

## Tests

```bash
make test                    # Python: pipeline unit + contract tests, no network
make p2-test                 # Python: analytics and ranking tests
cd web && npm run typecheck  # TypeScript
cd web && npm test           # component and rendering-rule tests
cd web && npm run test:e2e   # Playwright, needs a built site
```

The web tests build against the committed synthetic package in
`docs/fixtures/phase3`, so you do not need a real export to work on the UI:

```bash
cd web
IMPACT_DATA_DIR=../docs/fixtures/phase3 IMPACT_ALLOW_FIXTURE=1 npm run dev
```

No test needs the network, a token, or the 1.4 GB clone. Tests marked
`integration` do, and are deselected by default.

## The rules this project is built on

These are not style preferences. Code that breaks one of them will not be
merged, and most of them are enforced by a test.

1. **Do not weaken the honesty contract.** `contracts/PHASE_3_CONTRACT.md` lists
   the things the UI must never do: render a sentence that is not a claim with a
   claim id, show a composite score, present an unknown as a zero. If a rule has
   to change, change it in the contract document first, with the reasoning, then
   in the code.
2. **`null` is never `0`.** A dimension nobody could assess is unknown. It is
   excluded from the comparison and widens the interval; it never lowers a
   position. Do not use a default value to make an aggregation tidier.
3. **Config over code.** Anything a user might reasonably want to change belongs
   in a config file, with a comment explaining the trade-off. Thresholds,
   weights, half-lives, caps and path conventions are all config.
4. **No secret reaches git, data, logs, the export or the browser.** Extend the
   safety scan if you add an output; never bypass it.
5. **Every stage stays resumable and idempotent.** A rerun must cost zero API
   budget. Cache on content, not on position.
6. **No em dashes in prose.** In sentences a human reads (Markdown, UI strings,
   comments, docstrings), write a comma. Single-glyph placeholders for a missing
   value, en dashes in date ranges, and ASCII diagrams are all fine.

## Changes that touch the export

The export schema is a contract between the pipeline and the site, and both
sides are checked against it:

- `contracts/PHASE_2_CONTRACT.md`, the pipeline's tables and join keys.
- `contracts/PHASE_3_CONTRACT.md`, the export shape and the rendering rules.

`tests/test_store_and_contract.py` fails if you export a table the contract does
not document, or if a non-negotiable rule stops being stated. If you add a field,
add it to the contract and to `web/src/lib/schema.ts` in the same change.

## Adding a forge adapter

_Placeholder. GitLab and Azure DevOps adapters land in a later change, and this
section will document the adapter protocol (`src/impact/forge/base.py`), the
capability declaration a new forge must publish, and how a missing capability
switches off the analyses that depend on it rather than approximating them._

For now: the seam is that an adapter produces the normalized tables described in
`contracts/PHASE_2_CONTRACT.md`. Nothing downstream of `normalize` knows which
forge the data came from, and it must stay that way.

## Adding a language parser

_Placeholder. Rust, Go, SQL and Ruby import parsers land in a later change, and
this section will document where a parser plugs in (`src/impact/graph/`), what
it must return, and how parser coverage is reported per language on the coverage
page._

For now: a language with no parser makes blast radius `unknown` for work
confined to it. That is the correct behaviour, and it is published on the
coverage page. A parser that guesses is worse than no parser.

## Pull requests

- One change per pull request, with a title that says what changed.
- Say what you measured. This project reports real numbers on real
  repositories; "faster" without a figure is not a claim.
- If you found a defect you are not fixing, open an issue for it rather than
  leaving a comment in the code.
- New behaviour needs a test. A bug fix needs the test that would have caught it.

## Disputing a claim

If the dashboard says something about your work that is wrong, open a **claim
dispute** issue with the `claim_id` shown next to the sentence. That is a
first-class bug, not a complaint. The claim id, its evidence and its derivation
are all in the published package, so a dispute is usually resolvable by reading
the evidence the claim rests on.
