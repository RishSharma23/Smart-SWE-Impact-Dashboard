"""Actor identity, clustering, and bot/AI classification.

Three separate questions, deliberately kept separate:

*Who is this?*  Git identities (name + email) and GitHub identities (login) are
merged with union-find.  Only evidence that is actually unique is used as a
merge key: a GitHub login, a normalised email, and the numeric user id embedded
in ``12345+login@users.noreply.github.com``.  Display name is **not** a merge
key -- two people share a first name, and a bad merge silently reassigns
someone's work.  Anything that only matched on a weak signal is marked
``ambiguous`` rather than merged.

*Is this a bot?*  A probability plus the list of reasons that produced it, so a
consumer can pick its own threshold.  ``[bot]`` login suffix and GraphQL
``__typename == Bot`` are authoritative; name-shaped guesses are not.

*Was AI involved?*  PostHog commits carry ``Co-authored-by: ...
noreply@anthropic.com`` and similar on a large share of commits.  That is not
"a bot did it" -- a human still opened and defended the PR -- so it is tracked
on its own axis and never folded into ``bot_probability``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..hashing import actor_id as make_actor_id
from ..versions import feature_version

GITHUB_NOREPLY_RE = re.compile(
    r"^(?:(?P<uid>\d+)\+)?(?P<login>[A-Za-z0-9-]+)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, key: str) -> None:
        self.parent.setdefault(key, key)

    def find(self, key: str) -> str:
        self.add(key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:  # path compression
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic root choice so reruns produce identical clusters.
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo


@dataclass
class ActorRecord:
    actor_id: str
    login: str | None = None
    display_name: str | None = None
    github_database_id: int | None = None
    github_typename: str | None = None
    emails: set[str] = field(default_factory=set)
    git_names: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    bot_reasons: list[str] = field(default_factory=list)
    bot_probability: float = 0.0
    is_ai_assistant: bool = False


class ActorResolver:
    def __init__(self, bots_cfg: Mapping[str, Any]) -> None:
        self.rules = list(bots_cfg.get("rules") or [])
        self.known_bots = {
            str(x).lower() for x in (bots_cfg.get("known_bot_logins") or [])
        }
        ai = bots_cfg.get("ai_assistant_identities") or {}
        self.ai_emails = {str(x).lower() for x in (ai.get("emails") or [])}
        self.ai_login_patterns = [
            re.compile(p, re.IGNORECASE) for p in (ai.get("login_patterns") or [])
        ]
        identity = bots_cfg.get("identity") or {}
        self.cluster_on_email = bool(identity.get("cluster_on_email", True))
        self.parse_noreply = bool(identity.get("parse_github_noreply_ids", True))

        self.records: dict[str, ActorRecord] = {}
        self.uf = UnionFind()
        # login -> set of emails seen with it, used to detect shared addresses.
        self._email_logins: dict[str, set[str]] = {}

    # -- ingestion -------------------------------------------------------

    def add_github_actor(
        self, actor: Mapping[str, Any] | None, *, source: str
    ) -> str | None:
        if not actor:
            return None
        login = (actor.get("login") or "").strip()
        if not login:
            return None
        key = make_actor_id(login)
        record = self.records.setdefault(key, ActorRecord(actor_id=key, login=login))
        record.login = login
        record.display_name = record.display_name or actor.get("name")
        if actor.get("databaseId"):
            record.github_database_id = int(actor["databaseId"])
        if actor.get("__typename"):
            record.github_typename = str(actor["__typename"])
        record.sources.add(source)
        self.uf.add(key)
        return key

    def add_git_identity(
        self, name: str | None, email: str | None, *, source: str
    ) -> str:
        email_norm = (email or "").strip().lower()
        name = (name or "").strip()

        login = None
        if self.parse_noreply and email_norm:
            match = GITHUB_NOREPLY_RE.match(email_norm)
            if match:
                login = match.group("login")

        if login:
            key = make_actor_id(login)
            record = self.records.setdefault(
                key, ActorRecord(actor_id=key, login=login)
            )
            record.login = login
        else:
            key = make_actor_id(None, email_norm or name)
            record = self.records.setdefault(key, ActorRecord(actor_id=key))

        if email_norm and EMAIL_RE.match(email_norm):
            record.emails.add(email_norm)
            self._email_logins.setdefault(email_norm, set())
            if record.login:
                self._email_logins[email_norm].add(record.login)
        if name:
            record.git_names.add(name)
        record.sources.add(source)
        self.uf.add(key)

        if email_norm in self.ai_emails:
            record.is_ai_assistant = True
        return key

    # -- clustering ------------------------------------------------------

    def _link_by_email(self) -> dict[str, list[str]]:
        """Merge identities that share a real email address.

        A shared address is normally one person with two Git configs.  When one
        address maps to *several distinct logins* it is a shared/role account,
        which is recorded as ambiguity instead of merging strangers together.
        """
        shared: dict[str, list[str]] = {}
        if not self.cluster_on_email:
            return shared
        by_email: dict[str, list[str]] = {}
        for key, record in self.records.items():
            for email in record.emails:
                by_email.setdefault(email, []).append(key)
        for email, keys in by_email.items():
            logins = {self.records[k].login for k in keys if self.records[k].login}
            if len(logins) > 1:
                shared[email] = sorted(logins)
                continue
            for other in keys[1:]:
                self.uf.union(keys[0], other)
        return shared

    # -- classification --------------------------------------------------

    def _classify_bot(self, record: ActorRecord) -> tuple[float, list[str]]:
        probability = 0.0
        reasons: list[str] = []
        login = (record.login or "").lower()

        for rule in self.rules:
            kind = rule.get("kind")
            hit = False
            if kind == "typename" and record.github_typename == rule.get("value"):
                hit = True
            elif kind == "login_regex" and login and re.search(
                str(rule.get("pattern", "")), login
            ):
                hit = True
            elif kind == "email_regex" and any(
                re.search(str(rule.get("pattern", "")), e) for e in record.emails
            ):
                hit = True
            elif kind == "login_in_list" and login in self.known_bots:
                hit = True
            if hit:
                probability = max(probability, float(rule.get("probability", 0.0)))
                reasons.append(f"{rule.get('id')}: {rule.get('reason')}")

        return round(min(1.0, probability), 3), reasons

    def _classify_ai(self, record: ActorRecord) -> bool:
        if record.is_ai_assistant:
            return True
        login = (record.login or "").lower()
        return bool(login) and any(p.search(login) for p in self.ai_login_patterns)

    # -- output ----------------------------------------------------------

    def finalize(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        shared_emails = self._link_by_email()

        clusters: dict[str, list[str]] = {}
        for key in self.records:
            clusters.setdefault(self.uf.find(key), []).append(key)

        rows: list[dict[str, Any]] = []
        for key, record in sorted(self.records.items()):
            cluster_root = self.uf.find(key)
            members = sorted(clusters.get(cluster_root, [key]))
            probability, reasons = self._classify_bot(record)
            is_ai = self._classify_ai(record)

            ambiguity: list[str] = []
            if not record.login:
                ambiguity.append("no_github_login: identified by git email only")
            if len(members) > 1:
                ambiguity.append(f"clustered_with:{len(members) - 1}")
            for email in record.emails:
                if email in shared_emails:
                    ambiguity.append(
                        "shared_email:" + ",".join(shared_emails[email][:4])
                    )
            if 0.0 < probability < 0.9:
                ambiguity.append("bot_classification_uncertain")

            display = record.display_name or (
                sorted(record.git_names)[0] if record.git_names else None
            )
            rows.append(
                {
                    "actor_id": record.actor_id,
                    "login": record.login,
                    "display_name": display,
                    "github_database_id": record.github_database_id,
                    "github_typename": record.github_typename,
                    "account_type": (
                        "bot" if probability >= 0.9
                        else "user" if record.login
                        else "git_identity"
                    ),
                    "bot_probability": probability,
                    "bot_reasons": reasons,
                    "is_bot": probability >= 0.9,
                    "is_ai_assistant_identity": is_ai,
                    # Only addresses already public in Git history are kept.
                    "emails": sorted(record.emails),
                    "email_count": len(record.emails),
                    "git_names": sorted(record.git_names),
                    "identity_cluster_id": cluster_root,
                    "identity_cluster_size": len(members),
                    "identity_cluster_members": members,
                    "ambiguity_status": "ambiguous" if ambiguity else "resolved",
                    "ambiguity_reasons": ambiguity,
                    "sources": sorted(record.sources),
                    "actor_identity_version": feature_version("actor_identity"),
                }
            )

        summary = {
            "actors": len(rows),
            "with_login": sum(1 for r in rows if r["login"]),
            "git_only": sum(1 for r in rows if not r["login"]),
            "bots": sum(1 for r in rows if r["is_bot"]),
            "ai_assistant_identities": sum(
                1 for r in rows if r["is_ai_assistant_identity"]
            ),
            "ambiguous": sum(1 for r in rows if r["ambiguity_status"] == "ambiguous"),
            "clusters": len(clusters),
            "multi_member_clusters": sum(1 for m in clusters.values() if len(m) > 1),
            "shared_emails": len(shared_emails),
        }
        return rows, summary

    def cluster_of(self, actor_id: str) -> str:
        return self.uf.find(actor_id)


def resolve_actor_ref(actor: Mapping[str, Any] | None) -> str | None:
    if not actor:
        return None
    login = (actor.get("login") or "").strip()
    return make_actor_id(login) if login else None


def index_by_id(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r["actor_id"]): dict(r) for r in rows}
