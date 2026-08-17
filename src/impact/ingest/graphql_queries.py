"""GraphQL documents and batch-query builders.

Two design choices worth stating, both measured against the live API rather
than assumed:

*Batched aliases.*  ``repository { p0: pullRequest(number:..) p1: ... }`` costs
the same 1-2 rate-limit points as a single-PR query (measured: 25 PR core
records = 1 point / nodeCount 500; 10 full review records = 2 points /
nodeCount 1400).  Batching is therefore what turns a multi-hour extraction into
a ~35 minute one.  The batch numbers are inlined into the document rather than
passed as variables, because GraphQL has no "list of aliases" construct; the
request hash covers the document text, so caching still works.

*Partial tolerance.*  A batch containing one deleted/never-existed PR returns
``data`` for the rest plus a ``NOT_FOUND`` entry in ``errors``.  Failing the
page would create a silent gap, so the client keeps the data and the ledger
keeps the error.
"""

from __future__ import annotations

from typing import Iterable

RATE_LIMIT = "rateLimit { cost remaining limit nodeCount resetAt }"

ACTOR = "{ login __typename ... on User { databaseId name } ... on Bot { databaseId } }"


REPOSITORY_QUERY = f"""
query RepoMeta($owner: String!, $name: String!) {{
  {RATE_LIMIT}
  repository(owner: $owner, name: $name) {{
    id databaseId nameWithOwner url isPrivate isFork isArchived
    createdAt pushedAt updatedAt diskUsage
    primaryLanguage {{ name }}
    licenseInfo {{ spdxId name }}
    defaultBranchRef {{
      name
      target {{ oid ... on Commit {{ committedDate message }} }}
    }}
  }}
}}
"""


# ---------------------------------------------------------------------------
# Discovery: search sliced by date so no slice exceeds the 1000-result ceiling.
# ---------------------------------------------------------------------------

SEARCH_QUERY = f"""
query Discover($q: String!, $cursor: String) {{
  {RATE_LIMIT}
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {{
    issueCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      __typename
      ... on PullRequest {{
        number state isDraft createdAt updatedAt closedAt mergedAt
      }}
      ... on Issue {{
        number state createdAt updatedAt closedAt
      }}
    }}
  }}
}}
"""


# ---------------------------------------------------------------------------
# PR core.  Everything cheap and scalar, plus the small connections.
# File lists and commit lists are deliberately NOT requested: PostHog
# squash-merges, so Git gives us both for free and with more detail
# (rename status, numstat, binary markers) than the API exposes.
# ---------------------------------------------------------------------------

PR_CORE_FIELDS = f"""
    number id databaseId url title bodyText state isDraft
    createdAt updatedAt closedAt mergedAt
    additions deletions changedFiles
    author {ACTOR}
    mergedBy {ACTOR}
    editor {{ login }}
    baseRefName headRefName baseRefOid headRefOid headRepositoryOwner {{ login }}
    mergeCommit {{ oid committedDate }}
    isCrossRepository maintainerCanModify
    milestone {{ title number state }}
    labels(first: 15) {{ totalCount nodes {{ name color }} }}
    assignees(first: 6) {{ totalCount nodes {{ login }} }}
    closingIssuesReferences(first: 8) {{ totalCount nodes {{ number title state url }} }}
    reviews {{ totalCount }}
    reviewThreads {{ totalCount }}
    comments {{ totalCount }}
    commits {{ totalCount }}
    files {{ totalCount }}
    reactions {{ totalCount }}
    participants(first: 10) {{ totalCount nodes {{ login }} }}
"""


PR_DETAIL_FIELDS = """
    number
    reviews(first: 20) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id databaseId state submittedAt createdAt updatedAt url bodyText
        author { login __typename }
        commit { oid }
      }
    }
    reviewThreads(first: 15) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id isResolved isOutdated isCollapsed
        resolvedBy { login }
        path line startLine originalLine diffSide subjectType
        comments(first: 6) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes {
            id databaseId url bodyText createdAt updatedAt
            author { login __typename }
            replyTo { id }
            originalCommit { oid }
            commit { oid }
            outdated
          }
        }
      }
    }
    comments(first: 15) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id databaseId url bodyText createdAt updatedAt
        author { login __typename }
      }
    }
    timelineItems(
      first: 25
      itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT, DISCONNECTED_EVENT, REFERENCED_EVENT]
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        __typename
        ... on CrossReferencedEvent {
          createdAt willCloseTarget
          source { __typename ... on PullRequest { number } ... on Issue { number } }
        }
        ... on ConnectedEvent {
          createdAt
          subject { __typename ... on PullRequest { number } ... on Issue { number } }
        }
        ... on ReferencedEvent { createdAt commit { oid } }
      }
    }
"""


ISSUE_FIELDS = """
    number id databaseId url title bodyText state stateReason
    createdAt updatedAt closedAt
    author { login __typename }
    labels(first: 15) { totalCount nodes { name } }
    assignees(first: 6) { totalCount nodes { login } }
    comments(first: 10) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { id url bodyText createdAt author { login __typename } }
    }
    timelineItems(first: 20, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {
      totalCount
      nodes {
        __typename
        ... on CrossReferencedEvent {
          createdAt willCloseTarget
          source { __typename ... on PullRequest { number } ... on Issue { number } }
        }
        ... on ConnectedEvent {
          createdAt
          subject { __typename ... on PullRequest { number } ... on Issue { number } }
        }
      }
    }
"""


def build_batch_query(
    numbers: Iterable[int],
    fields: str,
    *,
    node: str = "pullRequest",
    operation: str = "Batch",
) -> str:
    """Build an aliased batch document for a list of PR/issue numbers."""
    numbers = list(numbers)
    if not numbers:
        raise ValueError("build_batch_query requires at least one number")
    aliases = "\n".join(
        f"    n{index}: {node}(number: {int(number)}) {{\n{fields}\n    }}"
        for index, number in enumerate(numbers)
    )
    return (
        f"query {operation}($owner: String!, $name: String!) {{\n"
        f"  {RATE_LIMIT}\n"
        f"  repository(owner: $owner, name: $name) {{\n"
        f"{aliases}\n"
        f"  }}\n"
        f"}}\n"
    )


def parse_batch_response(payload: dict, count: int) -> list[dict]:
    """Pull the aliased nodes back out, dropping aliases that resolved to null.

    A null alias means the number does not exist (or was deleted).  That is a
    real fact about the repository, not an error to hide, so the caller records
    it via the ledger's stored ``errors`` block.
    """
    repo = (payload.get("data") or {}).get("repository") or {}
    out = []
    for index in range(count):
        node = repo.get(f"n{index}")
        if node:
            out.append(node)
    return out


# ---------------------------------------------------------------------------
# Overflow pagination for the rare PR with more than one page of a connection.
# Skipping these would silently truncate exactly the biggest, most-discussed
# PRs -- the ones that matter most to an impact analysis.
# ---------------------------------------------------------------------------

PR_REVIEW_THREADS_PAGE = f"""
query ThreadPage($owner: String!, $name: String!, $number: Int!, $cursor: String) {{
  {RATE_LIMIT}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      number
      reviewThreads(first: 50, after: $cursor) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id isResolved isOutdated isCollapsed
          resolvedBy {{ login }}
          path line startLine originalLine diffSide subjectType
          comments(first: 10) {{
            totalCount
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              id databaseId url bodyText createdAt updatedAt outdated
              author {{ login __typename }}
              replyTo {{ id }}
              originalCommit {{ oid }}
              commit {{ oid }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

PR_REVIEWS_PAGE = f"""
query ReviewPage($owner: String!, $name: String!, $number: Int!, $cursor: String) {{
  {RATE_LIMIT}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      number
      reviews(first: 100, after: $cursor) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id databaseId state submittedAt createdAt updatedAt url bodyText
          author {{ login __typename }}
          commit {{ oid }}
        }}
      }}
    }}
  }}
}}
"""

PR_COMMENTS_PAGE = f"""
query CommentPage($owner: String!, $name: String!, $number: Int!, $cursor: String) {{
  {RATE_LIMIT}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      number
      comments(first: 100, after: $cursor) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id databaseId url bodyText createdAt updatedAt
          author {{ login __typename }}
        }}
      }}
    }}
  }}
}}
"""
