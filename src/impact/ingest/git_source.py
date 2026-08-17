"""Git extraction: commit DAG, identities, trailers, and per-file diffs.

PostHog squash-merges every pull request, so the analysed branch is a linear
chain in which **one commit == one merged PR**.  That is verified at run time
(``linear_history`` / ``parent_count_distribution`` in the manifest) rather
than assumed, because the whole file-level layer depends on it: changed paths,
rename detection, per-file line counts and binary markers all come from Git for
free, which is why the GitHub budget can be spent on review data instead.

One ``git log --raw --numstat -z`` pass over the 90-day window costs ~45s
locally and yields everything in this module.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..config import Settings, iso, parse_ts
from ..hashing import commit_id, sha256_text
from ..versions import EXTRACTOR_VERSION

log = logging.getLogger("impact.git")

UTC = dt.timezone.utc

# Record/field separators chosen because they cannot appear in a git path and
# are vanishingly unlikely in a commit message.
REC = "\x1e"
FLD = "\x1f"

RENAME_LIMIT = 3000

# git trailers we care about; matched case-insensitively at line start.
TRAILER_RE = re.compile(
    r"^(?P<key>[A-Za-z][A-Za-z-]{1,40})\s*:\s*(?P<value>.+?)\s*$", re.MULTILINE
)
CO_AUTHOR_RE = re.compile(
    r"^co-authored-by:\s*(?P<name>.*?)\s*<(?P<email>[^>]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Two revert spellings occur here. git's own `Revert "<subject>"` is the rarer
# one in PostHog: because every merge is a squash with a conventional title,
# reverts usually arrive as `revert(scope): <subject>`. Matching only the git
# form reports zero reverts on this repository, which is why both are handled.
REVERT_SUBJECT_RE = re.compile(r'^revert\s+"(?P<subject>.*)"\s*$', re.IGNORECASE)
REVERT_CONVENTIONAL_RE = re.compile(
    r"^revert(\([^)]*\))?!?:\s*(?P<subject>.+?)\s*$", re.IGNORECASE
)
REVERT_BODY_RE = re.compile(
    r"this reverts commit\s+(?P<sha>[0-9a-f]{7,40})", re.IGNORECASE
)
CHERRY_PICK_RE = re.compile(
    r"cherry picked from commit\s+(?P<sha>[0-9a-f]{7,40})", re.IGNORECASE
)
# GitHub squash-merge suffix: "title (#12345)". Corroborating evidence only --
# the authoritative PR<->commit mapping comes from GraphQL mergeCommit.oid.
PR_SUFFIX_RE = re.compile(r"\(#(?P<number>\d{1,7})\)\s*$")
NUMSTAT_RE = re.compile(r"^(?P<add>\d+|-)\t(?P<del>\d+|-)\t(?P<rest>.*)$", re.DOTALL)


class GitError(RuntimeError):
    pass


def run_git(repo: Path, args: list[str], *, timeout: int = 1800) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args[:4])} failed ({proc.returncode}): {proc.stderr[:600]}"
        )
    return proc.stdout


def run_git_bytes(repo: Path, args: list[str], *, timeout: int = 3600) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args[:4])} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace')[:600]}"
        )
    return proc.stdout


# ---------------------------------------------------------------------------
# Clone management
# ---------------------------------------------------------------------------


def ensure_clone(settings: Settings, *, force: bool = False) -> dict[str, Any]:
    """Clone the analysis source if needed and describe what we ended up with.

    The clone is never modified and never built (principle 8).  Strategy is
    recorded so a reader knows exactly which history was available.
    """
    repo = settings.clone_path
    strategy = settings.clone.get("strategy", "shallow_since")
    url = settings.repository["url"]
    branch = settings.default_branch

    if force and repo.exists():
        import shutil

        shutil.rmtree(repo)

    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--no-tags"]
        if settings.clone.get("single_branch", True):
            args += ["--single-branch", "--branch", branch]
        if strategy == "shallow_since":
            buffer_days = int(settings.clone.get("shallow_since_buffer_days", 30))
            since = (settings.window.start - dt.timedelta(days=buffer_days)).date()
            args.append(f"--shallow-since={since.isoformat()}")
        elif strategy == "blob_none":
            args.append("--filter=blob:none")
        args += [url, str(repo)]
        log.info("cloning %s (%s) -> %s", url, strategy, repo)
        proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5400)
        if proc.returncode != 0:
            raise GitError(f"clone failed: {proc.stderr[-1500:]}")

    head = run_git(repo, ["rev-parse", "HEAD"]).strip()
    head_date = run_git(repo, ["log", "-1", "--format=%cI"]).strip()
    is_shallow = run_git(repo, ["rev-parse", "--is-shallow-repository"]).strip() == "true"
    commit_count = int(run_git(repo, ["rev-list", "--count", "HEAD"]).strip())
    oldest = run_git(repo, ["log", "--reverse", "--format=%cI", "--max-count=1"]).strip()

    # Verify the linear-history assumption instead of trusting it.
    parents = run_git(repo, ["log", "--format=%p"]).splitlines()
    dist: dict[str, int] = {}
    for line in parents:
        key = str(len(line.split()))
        dist[key] = dist.get(key, 0) + 1

    return {
        "repository_url": url,
        "default_branch": branch,
        "analyzed_head_sha": head,
        "analyzed_head_committed_at": iso(parse_ts(head_date)),
        "clone_strategy": strategy,
        "is_shallow": is_shallow,
        "local_path": str(repo),
        "commit_count_available": commit_count,
        "oldest_available_commit_at": iso(parse_ts(oldest)) if oldest else None,
        "parent_count_distribution": dist,
        "linear_history": set(dist) <= {"0", "1"},
        "fetched_at": iso(dt.datetime.now(UTC)),
        "extractor_version": EXTRACTOR_VERSION,
    }


def snapshot_config_files(settings: Settings, head_sha: str) -> list[dict[str, Any]]:
    """Capture ownership/layout config at the analysed commit, with hashes.

    Missing files are recorded with ``status='missing'`` -- PostHog has no
    ``CODEOWNERS-soft``, and that absence is itself a finding worth carrying
    into the mapping report rather than a silent skip.
    """
    repo = settings.clone_path
    rows: list[dict[str, Any]] = []

    wanted = list(settings.snapshot_files)
    # Distributed ownership: PostHog resolves the *nearest* owners.yaml, and
    # per-directory AGENTS.md carry path-local instructions. Both are discovered
    # dynamically because their locations move.
    tracked = run_git(repo, ["ls-files"]).splitlines()
    wanted += [p for p in tracked if p.endswith("owners.yaml")]
    wanted += [p for p in tracked if p.endswith("AGENTS.md")]
    wanted += [p for p in tracked if re.fullmatch(r"products/[^/]+/manifest\.tsx?", p)]

    for path in sorted(dict.fromkeys(wanted)):
        try:
            content = run_git(repo, ["show", f"{head_sha}:{path}"], timeout=60)
            rows.append(
                {
                    "path": path,
                    "status": "present",
                    "commit_sha": head_sha,
                    "size_bytes": len(content.encode("utf-8")),
                    "content_sha256": sha256_text(content),
                    "unavailable_reason": None,
                }
            )
        except (GitError, subprocess.TimeoutExpired) as exc:
            rows.append(
                {
                    "path": path,
                    "status": "missing",
                    "commit_sha": head_sha,
                    "size_bytes": None,
                    "content_sha256": None,
                    "unavailable_reason": str(exc)[:200],
                }
            )
    return rows


def read_file_at(settings: Settings, sha: str, path: str) -> str | None:
    try:
        return run_git(settings.clone_path, ["show", f"{sha}:{path}"], timeout=60)
    except (GitError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------------------
# Commit metadata
# ---------------------------------------------------------------------------


@dataclass
class Trailers:
    co_authors: list[dict[str, str]]
    all_trailers: dict[str, list[str]]
    revert_of_subject: str | None
    revert_of_sha: str | None
    cherry_pick_of_sha: str | None


def parse_trailers(message: str) -> Trailers:
    co_authors = [
        {"name": m.group("name").strip(), "email": m.group("email").strip().lower()}
        for m in CO_AUTHOR_RE.finditer(message)
    ]
    trailers: dict[str, list[str]] = {}
    # Only the trailing block of a commit message holds real trailers; scanning
    # the whole body would capture prose like "Note: this is slow".
    tail = "\n".join(message.strip().splitlines()[-15:])
    for match in TRAILER_RE.finditer(tail):
        key = match.group("key").strip().lower()
        if key in {
            "co-authored-by", "signed-off-by", "reviewed-by", "acked-by",
            "tested-by", "reported-by", "fixes", "closes", "refs",
            "cherry-picked-from", "change-id",
        }:
            trailers.setdefault(key, []).append(match.group("value").strip())

    lines = message.strip().splitlines()
    subject = lines[0] if lines else ""
    revert_subject = None
    revert_match = REVERT_SUBJECT_RE.match(subject.strip()) or (
        REVERT_CONVENTIONAL_RE.match(subject.strip())
    )
    if revert_match:
        revert_subject = revert_match.group("subject")
        # The squash suffix "(#12345)" is part of the merge, not of the
        # reverted change's title; strip it so subject matching can work.
        revert_subject = PR_SUFFIX_RE.sub("", revert_subject).strip()
    body_revert = REVERT_BODY_RE.search(message)
    cherry = CHERRY_PICK_RE.search(message)

    return Trailers(
        co_authors=co_authors,
        all_trailers=trailers,
        revert_of_subject=revert_subject,
        revert_of_sha=body_revert.group("sha") if body_revert else None,
        cherry_pick_of_sha=cherry.group("sha") if cherry else None,
    )


def iter_commit_metadata(
    settings: Settings, *, since: dt.datetime, until: dt.datetime | None = None
) -> Iterator[dict[str, Any]]:
    """Stream commit records for the analysed branch."""
    fmt = REC + FLD.join(
        ["%H", "%P", "%T", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%G?", "%s", "%B"]
    )
    args = ["log", f"--format={fmt}", f"--since={iso(since)}"]
    if until:
        args.append(f"--until={iso(until)}")
    out = run_git(settings.clone_path, args)

    qualifier = settings.qualifier
    for chunk in out.split(REC):
        if not chunk.strip():
            continue
        fields = chunk.split(FLD)
        if len(fields) < 12:
            log.warning("skipping malformed commit record (%d fields)", len(fields))
            continue
        (sha, parents, tree, an, ae, ai, cn, ce, ci, gpg, subject, body) = fields[:12]
        sha = sha.strip()
        message = body
        trailers = parse_trailers(message)
        pr_suffix = PR_SUFFIX_RE.search(subject.strip())
        parent_list = parents.split()

        yield {
            "commit_id": commit_id(sha, qualifier),
            "commit_sha": sha,
            "tree_sha": tree.strip(),
            "parent_shas": parent_list,
            "parent_count": len(parent_list),
            "is_merge_commit": len(parent_list) > 1,
            "author_name": an,
            "author_email": ae.strip().lower(),
            "authored_at": iso(parse_ts(ai)),
            "committer_name": cn,
            "committer_email": ce.strip().lower(),
            "committed_at": iso(parse_ts(ci)),
            "author_is_committer": ae.strip().lower() == ce.strip().lower(),
            "gpg_status": gpg.strip() or None,
            "subject": subject,
            "message": message,
            "message_sha256": sha256_text(message),
            "co_authors": trailers.co_authors,
            "co_author_count": len(trailers.co_authors),
            "trailers": trailers.all_trailers,
            "is_revert": bool(trailers.revert_of_subject or trailers.revert_of_sha),
            "revert_of_subject": trailers.revert_of_subject,
            "revert_of_sha": trailers.revert_of_sha,
            "is_cherry_pick": bool(trailers.cherry_pick_of_sha),
            "cherry_pick_of_sha": trailers.cherry_pick_of_sha,
            # Corroborating only; authoritative mapping is GraphQL mergeCommit.
            "pr_number_from_subject": int(pr_suffix.group("number")) if pr_suffix else None,
            "extractor_version": EXTRACTOR_VERSION,
        }


# ---------------------------------------------------------------------------
# Per-file diffs
# ---------------------------------------------------------------------------


def _split_nul(blob: bytes) -> list[str]:
    return blob.decode("utf-8", errors="replace").split("\0")


def iter_commit_files(
    settings: Settings, *, since: dt.datetime, until: dt.datetime | None = None
) -> Iterator[dict[str, Any]]:
    """Stream one record per (commit, file).

    Parses the combined ``--raw --numstat -z`` stream.  ``--raw`` supplies the
    change status and blob SHAs (tree-only, cheap); ``--numstat`` supplies line
    counts and the binary marker (``-``).  Merging them in one pass avoids a
    second walk over 13k commits.
    """
    fmt = f"{REC}C%H"
    args = [
        "-c", f"diff.renameLimit={RENAME_LIMIT}",
        "log", f"--format={fmt}",
        "--raw", "--numstat", "-z", "-M", "--no-abbrev", "--no-color",
        f"--since={iso(since)}",
    ]
    if until:
        args.append(f"--until={iso(until)}")
    tokens = _split_nul(run_git_bytes(settings.clone_path, args))

    qualifier = settings.qualifier
    sha: str | None = None
    # (path) -> record, per commit; --raw arrives before --numstat for a commit.
    pending: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def flush() -> Iterator[dict[str, Any]]:
        for key in order:
            yield pending[key]

    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]

        if REC in token:
            # A commit marker may be glued to the tail of the previous token.
            marker = token[token.index(REC) :]
            if sha is not None:
                yield from flush()
            pending, order = {}, []
            sha = marker[2:].strip().strip("\n")
            index += 1
            continue

        stripped = token.strip("\n")
        if not stripped:
            index += 1
            continue

        if stripped.startswith(":"):
            # :<oldmode> <newmode> <oldsha> <newsha> <status>
            parts = stripped[1:].split()
            if len(parts) < 5:
                index += 1
                continue
            old_mode, new_mode, old_blob, new_blob, status = parts[:5]
            code = status[0].upper()
            score = status[1:] or None
            if code in {"R", "C"}:
                old_path = tokens[index + 1] if index + 1 < total else ""
                new_path = tokens[index + 2] if index + 2 < total else ""
                index += 3
            else:
                old_path = new_path = tokens[index + 1] if index + 1 < total else ""
                index += 2
            key = new_path or old_path
            record = pending.setdefault(key, {})
            if key not in order:
                order.append(key)
            record.update(
                {
                    "commit_id": commit_id(sha or "", qualifier),
                    "commit_sha": sha,
                    "path": new_path or old_path,
                    "old_path": old_path if code in {"R", "C"} else (old_path if code != "A" else None),
                    "new_path": new_path if code != "D" else None,
                    "change_status": code,
                    "similarity_score": int(score) if score and score.isdigit() else None,
                    "old_mode": old_mode,
                    "new_mode": new_mode,
                    "old_blob_sha": None if set(old_blob) == {"0"} else old_blob,
                    "new_blob_sha": None if set(new_blob) == {"0"} else new_blob,
                    "is_submodule": new_mode == "160000" or old_mode == "160000",
                }
            )
            continue

        match = NUMSTAT_RE.match(stripped)
        if match:
            add_raw, del_raw = match.group("add"), match.group("del")
            rest = match.group("rest")
            if rest == "":
                old_path = tokens[index + 1] if index + 1 < total else ""
                new_path = tokens[index + 2] if index + 2 < total else ""
                index += 3
                key = new_path or old_path
            else:
                key = rest
                index += 1
            record = pending.setdefault(key, {})
            if key not in order:
                order.append(key)
            record.setdefault("commit_id", commit_id(sha or "", qualifier))
            record.setdefault("commit_sha", sha)
            record.setdefault("path", key)
            is_binary = add_raw == "-" or del_raw == "-"
            record.update(
                {
                    "is_binary": is_binary,
                    "additions": None if is_binary else int(add_raw),
                    "deletions": None if is_binary else int(del_raw),
                    # Principle 5: unavailable is not zero.
                    "line_counts_unavailable_reason": "binary_file" if is_binary else None,
                }
            )
            continue

        index += 1

    if sha is not None:
        yield from flush()


# Diff content that could introduce, remove or read a PostHog feature flag.
# Passed to `git log -G`, which filters to commits whose diff actually adds or
# removes a matching line.
FLAG_DIFF_PATTERN = (
    r"FEATURE_FLAGS\.|isFeatureEnabled|useFeatureFlag|getFeatureFlag|"
    r"feature_enabled|featureFlags\["
)


def iter_flag_diffs(
    settings: Settings,
    *,
    since: dt.datetime,
    until: dt.datetime | None = None,
    pattern: str = FLAG_DIFF_PATTERN,
    max_chars: int = 400_000,
) -> Iterator[dict[str, Any]]:
    """Stream diffs for commits that touch a feature-flag reference.

    Feature-flag evidence is only meaningful on *added and removed* lines --
    a flag sitting in surrounding context says nothing about the change. That
    needs patch text, but patching all ~13k window commits costs ~20 minutes of
    subprocess churn for data that is 93% irrelevant.

    ``git log -G<regex>`` filters to the commits whose diff actually contains a
    matching line (measured here: 870 of 13,118, found in ~70s), and ``-U0``
    drops context lines so the output stays small. One pass, no per-commit
    subprocess.
    """
    args = [
        "-c", f"diff.renameLimit={RENAME_LIMIT}",
        "log", f"--format={REC}C%H",
        "-G", pattern,
        "--patch", "-U0", "--no-color", "--no-prefix",
        f"--since={iso(since)}",
    ]
    if until:
        args.append(f"--until={iso(until)}")
    out = run_git_bytes(settings.clone_path, args).decode("utf-8", errors="replace")

    for chunk in out.split(REC):
        if not chunk.strip():
            continue
        newline = chunk.find("\n")
        if newline == -1:
            continue
        sha = chunk[1:newline].strip()
        diff = chunk[newline + 1 :]
        truncated = len(diff) > max_chars
        yield {
            "commit_sha": sha,
            "diff_text": diff[:max_chars] if truncated else diff,
            "diff_chars": len(diff),
            "truncated": truncated,
            "unavailable_reason": (
                f"diff_truncated_at_{max_chars}_chars" if truncated else None
            ),
            "filter_pattern": pattern,
        }


def collect_patches(
    settings: Settings,
    shas: Iterable[str],
    *,
    max_lines: int = 400,
    max_files: int = 25,
    max_bytes: int = 120_000,
) -> Iterator[dict[str, Any]]:
    """Fetch patch text for commits small enough to be worth storing.

    Oversized and binary diffs get a row with ``patch_text=None`` and an
    explicit ``unavailable_reason`` rather than being omitted, so a consumer can
    tell "no patch stored" from "no patch exists".
    """
    repo = settings.clone_path
    for sha in shas:
        try:
            stat = run_git(
                repo, ["show", "--format=", "--numstat", "--no-color", sha], timeout=120
            )
        except (GitError, subprocess.TimeoutExpired) as exc:
            yield {
                "commit_sha": sha, "patch_text": None, "patch_bytes": None,
                "patch_sha256": None, "unavailable_reason": f"git_error: {exc}"[:200],
            }
            continue

        lines = [ln for ln in stat.splitlines() if ln.strip()]
        total_lines, binary = 0, False
        for line in lines:
            cols = line.split("\t")
            if len(cols) >= 2:
                if cols[0] == "-" or cols[1] == "-":
                    binary = True
                else:
                    total_lines += int(cols[0]) + int(cols[1])

        reason = None
        if len(lines) > max_files:
            reason = f"too_many_files:{len(lines)}>{max_files}"
        elif total_lines > max_lines:
            reason = f"too_many_lines:{total_lines}>{max_lines}"
        elif binary:
            reason = "contains_binary_diff"

        if reason:
            yield {
                "commit_sha": sha, "patch_text": None, "patch_bytes": None,
                "patch_sha256": None, "unavailable_reason": reason,
            }
            continue

        try:
            patch = run_git(
                repo,
                ["show", "--format=", "--patch", "--no-color", "-U3", sha],
                timeout=120,
            )
        except (GitError, subprocess.TimeoutExpired) as exc:
            yield {
                "commit_sha": sha, "patch_text": None, "patch_bytes": None,
                "patch_sha256": None, "unavailable_reason": f"git_error: {exc}"[:200],
            }
            continue

        encoded = patch.encode("utf-8")
        if len(encoded) > max_bytes:
            yield {
                "commit_sha": sha, "patch_text": None, "patch_bytes": len(encoded),
                "patch_sha256": sha256_text(patch),
                "unavailable_reason": f"patch_bytes:{len(encoded)}>{max_bytes}",
            }
            continue

        yield {
            "commit_sha": sha,
            "patch_text": patch,
            "patch_bytes": len(encoded),
            "patch_sha256": sha256_text(patch),
            "unavailable_reason": None,
        }
