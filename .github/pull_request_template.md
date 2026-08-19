## What this changes

<!-- One paragraph. What behaviour is different after this pull request. -->

## What you measured

<!--
Real numbers, on a real repository, where the change could affect them:
package size, build time, row counts, ranking positions, test counts.
"Faster" without a figure is not a claim. Write "not applicable" if it truly is.
-->

## Checklist

- [ ] I have read [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] Tests pass: `make test`, `make p2-test`, and in `web/`, `npm run typecheck` and `npm test`.
- [ ] New behaviour has a test. A bug fix has the test that would have caught it.
- [ ] No new tunable is hard-coded. Anything a user might change is in a config file with a comment.
- [ ] `null` still means unknown, and unknown is never rendered or aggregated as zero.
- [ ] Nothing weakens the rules in `contracts/PHASE_3_CONTRACT.md`. If a rule changed, the contract changed first.
- [ ] No secret can reach git, data, a log, the export or the browser.
- [ ] No em dashes in prose.
