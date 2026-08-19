# Security policy

## Reporting a vulnerability

Report privately. Do not open a public issue for a security problem.

Use this repository's **Security > Advisories > Report a vulnerability** form.
It is visible only to the maintainers. If that form is unavailable to you,
contact the maintainer through their GitHub profile and ask for a private
channel before sending any detail.

Please include what you were running, what you observed, and the smallest
reproduction you have. You will get an acknowledgement within seven days. This
is a small project maintained by one person, so a fix may take longer than that,
but you will be told where it stands.

Report against the latest commit on the default branch. There is no long-term
support branch yet, and no backports.

## What counts as a vulnerability here

This tool reads a repository, writes files on your machine, and produces a
static site. The things worth reporting are therefore:

- a credential reaching a file, a log, the export, the built site, or a
  third-party API,
- a path in which private repository content is published without the explicit
  opt-in described below,
- code execution triggered by data read from an analysed repository, an API
  response, or a language-model response,
- a way to make the safety scan pass on a package that carries a secret.

## How tokens are handled

These are properties of the code, not aspirations. If you find one of them is
not true, that is a vulnerability report.

- **Read-only scopes only.** The documented token is a fine-grained personal
  access token scoped to public repositories, read-only. No stage requests, uses
  or needs a write scope. The analysed repository is cloned and read; it is
  never built, modified or pushed to.
- **Never written to `.git/config`.** The clone is made from the plain
  `https://` repository URL with no credential embedded, so git has nothing to
  persist. The token travels only as an `Authorization` header on API calls.
- **Never persisted by the process.** The token is read from `GITHUB_TOKEN` in
  the environment, from `.env` (which is git-ignored), or from an authenticated
  `gh` CLI. The client dataclass holds it with `repr=False` so it cannot appear
  in a traceback or a debug dump.
- **Never in a log.** Request logging records the URL, the status and the rate
  limit, not the headers.
- **Never in the export.** Before the static package is written, every JSON file
  in it is scanned for GitHub tokens, fine-grained tokens, provider API keys,
  AWS keys, local filesystem paths and email addresses
  (`FORBIDDEN_PATTERNS` in `src/impact2/export.py`). A hit sets
  `publishable: false` and blocks the deploy. That gate is never bypassed,
  including under standing approval.
- **Redacted before any language-model call.** The optional LLM layer redacts
  email addresses and token-shaped strings from every payload before it leaves
  the process, and drops the fields named in
  `llm.controls.redaction.forbidden_fields` outright.

## Private repositories

Analysing a private or internal repository produces an export containing pull
request titles, review comment excerpts and contributor logins. Publishing that
export publishes all of it. Treat the generated `artifacts/` directory and the
built site as being as sensitive as the repository they describe.

## Third-party services

The pipeline talks to your forge (GitHub today, GitLab and Azure DevOps later)
and, only if you configure one, a language-model provider. There is no
telemetry, no analytics and no phone-home. Nothing is sent anywhere you have not
configured.
