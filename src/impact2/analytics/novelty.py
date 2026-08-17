"""C: novelty and replaceability evidence.

The question is what *kind* of change this was relative to the repository that
already existed: a new capability, an extension of something already there, a
simplification that removed complexity, or a repeat visit to an area that keeps
needing attention.

The phase spec attaches a warning to this analysis and it is worth restating in
the code: **novelty is not quality.**  A brand-new module can be a mistake and
a one-line extension can be the most valuable change of the quarter.  So this
module emits a *class* plus its evidence, ``may_set_band_alone`` is false in
config, and no rubric rule anywhere raises a band on novelty alone — novelty
only ever corroborates a band that other evidence already supports.

"Prior repository state" is bounded by what Phase 1 could see: a shallow clone
starting 30 days before the window.  A file that looks new here may be old and
simply untouched.  That limitation is attached to every record rather than
mentioned once in a footnote.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config
from ..versions import derivation_version

log = logging.getLogger("impact2.analytics.novelty")

VERSION = derivation_version("novelty")

NEW_PRODUCT_MANIFEST = re.compile(r"^products/([^/]+)/manifest\.tsx?$")
NEW_TOP_LEVEL_PRODUCT = re.compile(r"^products/([^/]+)/")
MIGRATION_CREATE_TABLE = re.compile(r"create[_\s]table|createtable", re.IGNORECASE)

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _production(row: Mapping[str, Any]) -> bool:
    if any(bool(row.get(flag)) for flag in MECHANICAL_FLAGS):
        return False
    return not (row.get("is_test") or row.get("is_docs"))


class NoveltyAnalyzer:
    def __init__(
        self,
        config: Phase2Config,
        *,
        prs: Mapping[int, Mapping[str, Any]],
        files_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        module_nodes: Mapping[str, Mapping[str, Any]],
        is_shallow_clone: bool,
    ) -> None:
        self.config = config
        self.prs = prs
        self.files_by_pr = files_by_pr
        self.nodes = module_nodes
        self.is_shallow = is_shallow_clone

        # Directories that already existed: any path touched by any PR whose
        # directory was not itself created in the window.
        self.existing_directories: set[str] = set()
        created_dirs: set[str] = set()
        for rows in files_by_pr.values():
            for row in rows:
                path = str(row.get("path") or "")
                if not path or "/" not in path:
                    continue
                directory = path.rsplit("/", 1)[0]
                if row.get("change_status") == "A":
                    created_dirs.add(directory)
                else:
                    self.existing_directories.add(directory)
        self.newly_created_directories = created_dirs - self.existing_directories

        # Title-token document frequency, for "unique problem framing".
        counts: dict[str, int] = defaultdict(int)
        for pr in prs.values():
            tokens = set(
                t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}",
                                              str(pr.get("title_subject") or ""))
            )
            for token in tokens:
                counts[token] += 1
        self.title_token_df = counts
        self.doc_count = max(1, len(prs))

        # How often each area is revisited, for maintenance_repeat.
        self.component_touch_counts: dict[str, int] = defaultdict(int)
        for rows in files_by_pr.values():
            for component in {str(r.get("component") or "unknown") for r in rows}:
                self.component_touch_counts[component] += 1

    def analyse(self, episode_id: str, pr_numbers: Sequence[int]) -> dict[str, Any]:
        files = [f for n in pr_numbers for f in self.files_by_pr.get(n, [])]
        production = [f for f in files if _production(f)]
        markers: list[str] = []
        evidence: list[dict[str, Any]] = []

        added = [f for f in production if f.get("change_status") == "A"]
        deleted = [f for f in production if f.get("change_status") == "D"]
        modified = [f for f in production if f.get("change_status") == "M"]

        # -- new capability markers --------------------------------------
        for row in added:
            path = str(row.get("path") or "")
            if NEW_PRODUCT_MANIFEST.match(path):
                markers.append("new_product_manifest")
                evidence.append({"marker": "new_product_manifest", "path": path})
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            if directory and directory in self.newly_created_directories:
                if "new_directory" not in markers:
                    markers.append("new_directory")
                    evidence.append({"marker": "new_directory", "path": directory})
            if row.get("is_migration") and MIGRATION_CREATE_TABLE.search(path):
                markers.append("new_migration_with_new_table")
                evidence.append({"marker": "new_migration_with_new_table", "path": path})

        top_level_products = {
            m.group(1) for f in added
            if (m := NEW_TOP_LEVEL_PRODUCT.match(str(f.get("path") or "")))
        }
        existing_products = {
            m.group(1) for f in modified
            if (m := NEW_TOP_LEVEL_PRODUCT.match(str(f.get("path") or "")))
        }
        for product in sorted(top_level_products - existing_products):
            markers.append("new_top_level_product_directory")
            evidence.append(
                {"marker": "new_top_level_product_directory", "product": product}
            )

        # -- simplification ------------------------------------------------
        net_deletions = sum(
            (row.get("deletions") or 0) - (row.get("additions") or 0)
            for row in production
            if not row.get("is_binary")
        )
        min_deletions = int(
            self.config.get("rubric.dimensions.decision_quality.simplification."
                            "min_net_production_deletions")
        )
        tests_kept = any(f.get("is_test") for f in files) or not any(
            f.get("is_test") and f.get("change_status") == "D" for f in files
        )
        is_simplification = (
            net_deletions >= min_deletions and bool(deleted) and tests_kept
        )
        if is_simplification:
            evidence.append(
                {
                    "marker": "net_production_removal",
                    "net_deleted_lines": int(net_deletions),
                    "files_deleted": len(deleted),
                }
            )

        # -- distinctive framing -------------------------------------------
        max_df_ratio = float(
            self.config.get("analytics.novelty.distinctive_token_max_document_frequency")
        )
        distinctive: list[str] = []
        for number in pr_numbers:
            subject = str((self.prs.get(number) or {}).get("title_subject") or "")
            for token in sorted(
                set(t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", subject))
            ):
                df = self.title_token_df.get(token, 0)
                if 0 < df / self.doc_count <= max_df_ratio and df <= 8:
                    distinctive.append(token)
        distinctive = sorted(set(distinctive))[:8]

        # -- maintenance repeat --------------------------------------------
        components = {str(f.get("component") or "unknown") for f in files}
        revisit_counts = [self.component_touch_counts.get(c, 0) for c in components]
        heavily_revisited = bool(revisit_counts) and max(revisit_counts) > max(
            20, int(0.02 * self.doc_count)
        )

        # -- classify --------------------------------------------------------
        if markers:
            novelty_class = "new_capability"
            rationale = f"introduces {', '.join(sorted(set(markers)))}"
        elif is_simplification:
            novelty_class = "simplification"
            rationale = (
                f"net removal of {int(net_deletions)} production lines across "
                f"{len(deleted)} deleted file(s) with tests intact"
            )
        elif added and not heavily_revisited:
            novelty_class = "extension"
            rationale = (
                f"adds {len(added)} production file(s) to existing directories"
            )
        elif heavily_revisited and not added:
            novelty_class = "maintenance_repeat"
            rationale = (
                "modifies components that are touched repeatedly across the window "
                "without adding new capability"
            )
        elif modified:
            novelty_class = "extension"
            rationale = f"modifies {len(modified)} existing production file(s)"
        else:
            novelty_class = "maintenance_repeat"
            rationale = "no production-code change observed"

        uncertainty: list[str] = []
        if self.is_shallow:
            uncertainty.append(
                "The clone is shallow: a file first seen inside the window may "
                "predate it. 'New' means new to the observed history, not new to "
                "the repository."
            )
        if not production:
            uncertainty.append("no production files to compare against prior state")

        return {
            "episode_id": episode_id,
            "novelty_class": novelty_class,
            "rationale": rationale,
            "markers": sorted(set(markers)),
            "evidence": evidence,
            "distinctive_title_tokens": distinctive,
            "has_unique_problem_framing": bool(distinctive),
            "net_production_line_delta": int(-net_deletions),
            "files_added": len(added),
            "files_deleted": len(deleted),
            "files_modified": len(modified),
            "is_simplification": is_simplification,
            "area_heavily_revisited": heavily_revisited,
            "uncertainty": uncertainty,
            "may_set_band_alone": False,
            "note": (
                "Novelty is evidence about the kind of change, not a judgement "
                "of its quality. No band is raised on novelty alone."
            ),
            "novelty_version": VERSION,
        }


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    by_class: dict[str, int] = defaultdict(int)
    for row in items:
        by_class[str(row.get("novelty_class"))] += 1
    return {
        "episodes_analysed": len(items),
        "by_class": dict(sorted(by_class.items())),
        "with_unique_framing": sum(1 for r in items if r.get("has_unique_problem_framing")),
        "simplifications": sum(1 for r in items if r.get("is_simplification")),
        "novelty_version": VERSION,
    }
