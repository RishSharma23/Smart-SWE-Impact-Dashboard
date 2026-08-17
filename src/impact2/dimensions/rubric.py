"""The six impact dimensions, evaluated per episode against ordinal bands.

Reading order for anyone auditing this file: the band rules are in
``config/phase2/rubric.yaml`` in English; the code below is that English made
executable, and every band it assigns comes back with the artifacts it read,
the corroboration it found, the counterevidence it found anyway, and a
confidence level.

Three rules hold across all six evaluators.

**No band is inferred from volume.**  There is no rule of the form "more PRs →
higher band".  Where a count appears it is a count of *distinct corroborating
artifact classes* or *distinct downstream components and authors* — breadth of
evidence, not amount of work.  The Collaboration evaluator additionally refuses
to see comment counts at all: its inputs are causally-confirmed interventions,
by construction.

**Band 3 needs corroboration; band 4 needs an explicit marker.**  Two distinct
artifact classes for band 3, three plus a textual marker and high confidence
for band 4.  This is what stops a large diff in a risky directory from being
read as a platform-wide reliability triumph.

**Unknown is not zero.**  A dimension with no *observable* evidence returns
band 0 with a reason.  A dimension whose evidence *could not be read* — the
diff is missing, the language has no parser, the window ended too early —
returns ``None`` and widens the interval instead of pushing the engineer down.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..config import Phase2Config, days_between, parse_ts
from ..ids import dimension_id, file_artifact, issue_artifact, pr_artifact
from ..versions import derivation_version

log = logging.getLogger("impact2.dimensions")

VERSION = derivation_version("dimension_rubric")

DIMENSIONS = (
    "product_outcome", "reliability_risk", "engineering_leverage",
    "decision_quality", "propagation_durability", "collaborative_amplification",
)

MECHANICAL_FLAGS = ("is_lockfile", "is_generated", "is_snapshot", "is_vendor",
                    "is_binary_asset")


def _production(row: Mapping[str, Any]) -> bool:
    if any(bool(row.get(flag)) for flag in MECHANICAL_FLAGS):
        return False
    return not (row.get("is_test") or row.get("is_docs"))


class Assessment(dict):
    """One dimension's verdict on one episode."""


def _assess(
    *,
    episode_id: str,
    dimension: str,
    band: int | None,
    rationale: str,
    evidence: Sequence[Mapping[str, Any]],
    artifact_classes: Sequence[str],
    counterevidence: Sequence[Mapping[str, Any]],
    confidence: str,
    confidence_reasons: Sequence[str],
    corroboration_status: str,
    unknown_reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Assessment:
    return Assessment(
        {
            "dimension_record_id": dimension_id(episode_id, dimension),
            "episode_id": episode_id,
            "dimension": dimension,
            "band": band,
            "band_label": (
                "unknown" if band is None
                else ["no_evidence", "local", "material", "broad", "transformative"][band]
            ),
            "is_unknown": band is None,
            "unknown_reason": unknown_reason,
            "rationale": rationale,
            "evidence": list(evidence),
            "evidence_count": len(evidence),
            "artifact_classes": sorted(set(artifact_classes)),
            "artifact_class_count": len(set(artifact_classes)),
            "corroboration_status": corroboration_status,
            "counterevidence": list(counterevidence),
            "counterevidence_count": len(counterevidence),
            "confidence": confidence,
            "confidence_reasons": list(confidence_reasons),
            "rubric_version": VERSION,
            **(dict(extra) if extra else {}),
        }
    )


class RubricEvaluator:
    def __init__(
        self,
        config: Phase2Config,
        *,
        prs: Mapping[int, Mapping[str, Any]],
        files_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        issues: Mapping[int, Mapping[str, Any]],
        change_shape: Mapping[int, Mapping[str, Any]],
        blast: Mapping[int, Mapping[str, Any]],
        regression: Mapping[int, Mapping[str, Any]],
        threads_by_pr: Mapping[int, Sequence[Mapping[str, Any]]],
        propagation: Mapping[str, Mapping[str, Any]],
        novelty: Mapping[str, Mapping[str, Any]],
        corrective: Mapping[str, Mapping[str, Any]],
        interventions: Mapping[str, Sequence[Mapping[str, Any]]],
        window_end: Any,
    ) -> None:
        self.config = config
        self.prs = prs
        self.files_by_pr = files_by_pr
        self.issues = issues
        self.change_shape = change_shape
        self.blast = blast
        self.regression = regression
        self.threads_by_pr = threads_by_pr
        self.propagation = propagation
        self.novelty = novelty
        self.corrective = corrective
        self.interventions = interventions
        self.window_end = parse_ts(window_end)

        self.rubric = config.get("rubric")
        self.corroboration = self.rubric["corroboration"]
        self.downgrades = self.rubric["confidence"]["downgrade_reasons"]
        self.levels = self.rubric["confidence"]["levels_by_downgrade"]

    # -- shared helpers ---------------------------------------------------
    def _episode_files(self, episode: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [f for n in episode.get("pr_numbers") or [] for f in self.files_by_pr.get(int(n), [])]

    def _confidence(
        self, episode: Mapping[str, Any], extra_reasons: Sequence[str] = ()
    ) -> tuple[str, list[str]]:
        """Confidence falls with each documented reason the evidence is thin."""
        reasons: list[str] = list(extra_reasons)
        steps = 0
        numbers = [int(n) for n in episode.get("pr_numbers") or []]

        if any(
            str((self.blast.get(n) or {}).get("reachability_band")) == "unknown"
            for n in numbers
        ):
            steps += int(self.downgrades["reachability_unknown"])
            reasons.append(
                "blast radius is unknown for at least one PR (unparsed language "
                "or no graph coverage)"
            )
        if any(
            (self.regression.get(n) or {}).get("requires_human_confirmation")
            for n in numbers
        ):
            steps += int(self.downgrades["requires_human_confirmation"])
            reasons.append(
                "corrective evidence is proximate-only and unconfirmed"
            )
        if any(
            (self.regression.get(n) or {}).get("survival_30d") is None
            and (self.regression.get(n) or {}).get("files_introduced")
            for n in numbers
        ):
            steps += int(self.downgrades["survival_unmeasurable"])
            reasons.append(
                "survival could not be measured: the window ends before the "
                "30-day checkpoint"
            )
        if any(
            t.get("comments_truncated")
            for n in numbers for t in self.threads_by_pr.get(n, [])
        ):
            steps += int(self.downgrades["thread_truncated"])
            reasons.append("at least one review thread was truncated at the pagination cap")
        if any(
            not (self.prs.get(n) or {}).get("has_merge_commit_in_clone")
            and (self.prs.get(n) or {}).get("merged_at")
            for n in numbers
        ):
            steps += int(self.downgrades["no_merge_commit_in_clone"])
            reasons.append(
                "a merged PR has no merge commit in the shallow clone, so its "
                "diff is unavailable"
            )
        if float(episode.get("cluster_confidence") or 1.0) < 0.55:
            steps += 1
            reasons.append(
                f"episode clustering confidence is {episode.get('cluster_confidence')}"
            )
        level = self.levels[min(steps, len(self.levels) - 1)]
        if not reasons:
            reasons.append("all supporting evidence is directly observable")
        return level, reasons

    def _cap_band(
        self,
        band: int,
        artifact_classes: Sequence[str],
        confidence: str,
        marker_present: bool,
    ) -> tuple[int, list[str]]:
        """Enforce the corroboration and marker requirements for bands 3 and 4."""
        notes: list[str] = []
        classes = len(set(artifact_classes))
        if band >= 4:
            need = int(self.corroboration["band_4_min_artifact_classes"])
            if classes < need:
                band = 3
                notes.append(
                    f"capped at 3: band 4 needs {need} corroborating artifact "
                    f"classes, found {classes}"
                )
            elif self.corroboration["band_4_requires_explicit_marker"] and not marker_present:
                band = 3
                notes.append("capped at 3: no explicit transformative marker in the text")
            elif self.corroboration["band_4_requires_high_confidence"] and confidence != "high":
                band = 3
                notes.append(f"capped at 3: band 4 requires high confidence, got {confidence}")
        if band >= 3:
            need = int(self.corroboration["band_3_min_artifact_classes"])
            if classes < need:
                band = 2
                notes.append(
                    f"capped at 2: band 3 needs {need} corroborating artifact "
                    f"classes, found {classes}"
                )
        return band, notes

    # ------------------------------------------------------------------
    # 1. Product / user outcome
    # ------------------------------------------------------------------
    def product_outcome(self, episode: Mapping[str, Any]) -> Assessment:
        eid = str(episode["episode_id"])
        numbers = [int(n) for n in episode.get("pr_numbers") or []]
        files = self._episode_files(episode)
        evidence: list[dict[str, Any]] = []
        classes: list[str] = []
        counter: list[dict[str, Any]] = list(episode.get("counterevidence") or [])

        production = [f for f in files if _production(f)]
        if not files:
            confidence, creasons = self._confidence(episode)
            return _assess(
                episode_id=eid, dimension="product_outcome", band=None,
                rationale="No file-level diff is available for this episode.",
                evidence=[], artifact_classes=[], counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_assessable",
                unknown_reason="no pr_files rows (merge commit outside the shallow clone)",
            )
        if not production:
            confidence, creasons = self._confidence(episode)
            return _assess(
                episode_id=eid, dimension="product_outcome", band=0,
                rationale=(
                    "No product or platform code changed — the episode touches only "
                    "tests, documentation, configuration or dependencies."
                ),
                evidence=[{"kind": "file_composition",
                           "detail": f"{len(files)} files, none of them production code"}],
                artifact_classes=[], counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_required",
            )

        # -- gather corroborating classes ---------------------------------
        if episode.get("issue_numbers"):
            classes.append("linked_issue")
            number = int(episode["issue_numbers"][0])
            issue = self.issues.get(number) or {}
            evidence.append(
                {
                    "kind": "linked_issue",
                    "artifact_id": issue_artifact(number),
                    "url": issue.get("url"),
                    "detail": f"closes issue #{number}: {str(issue.get('title') or '')[:120]}",
                }
            )
        if episode.get("feature_flag_keys"):
            classes.append("feature_flag")
            evidence.append(
                {
                    "kind": "feature_flag",
                    "detail": f"ships behind feature flag(s) "
                              f"{episode['feature_flag_keys'][:3]}",
                }
            )
        if episode.get("doc_file_count"):
            classes.append("docs_or_changelog")
            evidence.append(
                {"kind": "docs_or_changelog",
                 "detail": f"{episode['doc_file_count']} documentation file(s) changed"}
            )
        propagation = self.propagation.get(eid) or {}
        if int(propagation.get("reach_pr_count") or 0) > 0:
            classes.append("downstream_adoption")
            evidence.append(
                {"kind": "downstream_adoption",
                 "detail": f"{propagation['reach_pr_count']} later change(s) depend on "
                           "what this introduced"}
            )
        if episode.get("test_file_count"):
            classes.append("test_coverage")
            evidence.append(
                {"kind": "test_coverage",
                 "detail": f"{episode['test_file_count']} test file(s) changed alongside"}
            )
        if int(episode.get("review_intervention_count") or 0) > 0:
            classes.append("review_thread")

        corroborated_title = [
            n for n in numbers
            if (self.change_shape.get(n) or {}).get("title_claim_corroborated") is True
        ]
        if corroborated_title:
            evidence.append(
                {
                    "kind": "title_claim_corroborated",
                    "artifact_id": pr_artifact(corroborated_title[0]),
                    "detail": str(
                        (self.change_shape.get(corroborated_title[0]) or {}).get(
                            "title_claim_note"
                        )
                    )[:200],
                }
            )

        products = list(episode.get("products") or [])
        components = [c for c in (episode.get("components") or []) if c != "unknown"]

        # -- transformative markers ---------------------------------------
        novelty = self.novelty.get(eid) or {}
        markers = set(novelty.get("markers") or [])
        launch_language = any(
            phrase in str((self.prs.get(n) or {}).get("body_text") or "").lower()
            for n in numbers
            for phrase in ("public beta", "general availability", " ga ", "we're launching",
                           "we are launching", "now available to all")
        )
        marker_present = bool(
            markers & {"new_product_manifest", "new_top_level_product_directory"}
        ) or launch_language
        if marker_present:
            evidence.append(
                {"kind": "transformative_marker",
                 "detail": f"markers: {sorted(markers) or 'explicit launch language in the PR body'}"}
            )

        # -- band ----------------------------------------------------------
        if len(products) >= 2 or (len(components) >= 3 and len(set(classes)) >= 2):
            band = 3
            rationale = (
                f"Production changes span {len(products) or len(components)} "
                f"{'products' if len(products) >= 2 else 'components'} with "
                f"{len(set(classes))} classes of corroborating evidence."
            )
        elif set(classes) & {"linked_issue", "feature_flag"} or corroborated_title:
            band = 2
            rationale = (
                "A user-facing problem is resolved or the change ships behind a "
                "named flag, within one product surface."
            )
        else:
            band = 1
            rationale = (
                f"Production code changed in {len(components) or 1} component with "
                "no linked issue, feature flag or documentation to corroborate a "
                "user-visible outcome."
            )
        if marker_present and band >= 3:
            band = 4
            rationale += " An explicit transformative marker is present."

        confidence, creasons = self._confidence(episode)
        if episode.get("release_corroboration") != "corroborated" and band >= 3:
            band = 2
            creasons.append(
                "capped at 2: release is not independently corroborated, and "
                "merging is not proof of user release"
            )
        band, caps = self._cap_band(band, classes, confidence, marker_present)
        creasons.extend(caps)

        return _assess(
            episode_id=eid, dimension="product_outcome", band=band,
            rationale=rationale, evidence=evidence, artifact_classes=classes,
            counterevidence=counter, confidence=confidence,
            confidence_reasons=creasons,
            corroboration_status=(
                "corroborated" if len(set(classes)) >= 2
                else "single_source" if classes else "uncorroborated"
            ),
            extra={"products_touched": products, "components_touched": components},
        )

    # ------------------------------------------------------------------
    # 2. Reliability / risk
    # ------------------------------------------------------------------
    def reliability_risk(self, episode: Mapping[str, Any]) -> Assessment:
        eid = str(episode["episode_id"])
        numbers = [int(n) for n in episode.get("pr_numbers") or []]
        files = self._episode_files(episode)
        counter = list(episode.get("counterevidence") or [])
        evidence: list[dict[str, Any]] = []
        classes: list[str] = []

        if not files:
            confidence, creasons = self._confidence(episode)
            return _assess(
                episode_id=eid, dimension="reliability_risk", band=None,
                rationale="No file-level diff is available for this episode.",
                evidence=[], artifact_classes=[], counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_assessable",
                unknown_reason="no pr_files rows",
            )

        surfaces = self.rubric["dimensions"]["reliability_risk"]["risk_surfaces"]
        high_surfaces, medium_surfaces = set(surfaces["high"]), set(surfaces["medium"])
        touched: set[str] = set()
        for number in numbers:
            shape = self.change_shape.get(number) or {}
            for surface in shape.get("risk_surfaces") or []:
                touched.add(str(surface))
        touched_high = touched & high_surfaces
        touched_medium = touched & medium_surfaces
        if touched:
            classes.append("risk_surface")
            evidence.append(
                {"kind": "risk_surface", "detail": f"touches {sorted(touched)}"}
            )

        prefixes = {str((self.prs.get(n) or {}).get("title_prefix")) for n in numbers}
        is_corrective = bool(prefixes & {"fix", "perf", "revert"})
        if is_corrective:
            evidence.append(
                {"kind": "corrective_change",
                 "detail": f"title prefixes present: {sorted(prefixes & {'fix', 'perf', 'revert'})}"}
            )
        if any(
            (self.regression.get(n) or {}).get("tests_added_with_fix") for n in numbers
        ):
            classes.append("test_coverage")
            evidence.append(
                {"kind": "test_coverage", "detail": "a fix in this episode added tests"}
            )
        if episode.get("issue_numbers"):
            classes.append("linked_issue")
        if any(f.get("is_migration") for f in files):
            classes.append("migration")
            evidence.append({"kind": "migration", "detail": "changes a migration"})

        safety_interventions = [
            i for i in self.interventions.get(eid, [])
            if set(i.get("consequential_classes") or [])
            & {"security", "privacy", "data_integrity", "migration_safety"}
        ]
        if safety_interventions:
            classes.append("review_safety_thread")
            evidence.append(
                {
                    "kind": "review_safety_thread",
                    "url": safety_interventions[0].get("url"),
                    "detail": f"{len(safety_interventions)} review comment(s) raised a "
                              "safety concern on this episode",
                }
            )

        # Incident evidence must be stated, never inferred from a fix title.
        incident = self.rubric["dimensions"]["reliability_risk"]["incident_markers"]
        incident_labels = {str(l).lower() for l in incident["labels"]}
        incident_hit: str | None = None
        for number in numbers:
            pr = self.prs.get(number) or {}
            labels = {str(l).lower() for l in (pr.get("labels") or [])}
            if labels & incident_labels:
                incident_hit = f"PR #{number} carries label(s) {sorted(labels & incident_labels)}"
                break
            text = (str(pr.get("title_raw") or "") + " " +
                    str(pr.get("body_text") or "")).lower()
            for phrase in incident["phrases"]:
                if str(phrase).lower() in text:
                    incident_hit = f"PR #{number} text contains '{phrase}'"
                    break
            if incident_hit:
                break
        for issue_number in episode.get("issue_numbers") or []:
            issue = self.issues.get(int(issue_number)) or {}
            labels = {str(l).lower() for l in (issue.get("labels") or [])}
            if labels & incident_labels:
                incident_hit = (
                    f"issue #{issue_number} carries label(s) {sorted(labels & incident_labels)}"
                )
                break
        if incident_hit:
            classes.append("incident_marker")
            evidence.append({"kind": "incident_marker", "detail": incident_hit})

        reach = str(episode.get("reachability_band") or "unknown")
        crosses = reach in {"cross_product", "platform_wide"}

        if not (touched or is_corrective or safety_interventions):
            confidence, creasons = self._confidence(episode)
            return _assess(
                episode_id=eid, dimension="reliability_risk", band=0,
                rationale=(
                    "No reliability-relevant evidence: no risk surface touched, no "
                    "corrective change, no safety concern raised in review."
                ),
                evidence=evidence, artifact_classes=classes, counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_required",
            )

        if touched_high and crosses:
            band = 3
            rationale = (
                f"Touches high-risk surfaces {sorted(touched_high)} with "
                f"{reach} reach."
            )
        elif touched_high or (is_corrective and len(episode.get("components") or []) > 1):
            band = 2
            rationale = (
                f"Consequential risk reduction: {sorted(touched_high) or 'a corrective change'}"
                + (f" across {len(episode.get('components') or [])} components"
                   if len(episode.get("components") or []) > 1 else "")
                + "."
            )
        elif touched_medium or is_corrective or safety_interventions:
            band = 1
            rationale = "Localized hardening within a single component."
        else:
            band = 1
            rationale = "Some reliability-relevant change, confined locally."

        if incident_hit and band >= 3:
            band = 4
            rationale += f" Explicit incident/security evidence: {incident_hit}."

        confidence, creasons = self._confidence(episode)
        if reach == "unknown" and band >= 3:
            band = 2
            creasons.append(
                "capped at 2: reachability is unknown, so a high-blast-radius "
                "claim cannot be supported"
            )
        band, caps = self._cap_band(band, classes, confidence, bool(incident_hit))
        creasons.extend(caps)

        return _assess(
            episode_id=eid, dimension="reliability_risk", band=band,
            rationale=rationale, evidence=evidence, artifact_classes=classes,
            counterevidence=counter, confidence=confidence,
            confidence_reasons=creasons,
            corroboration_status=(
                "corroborated" if len(set(classes)) >= 2
                else "single_source" if classes else "uncorroborated"
            ),
            extra={"risk_surfaces_touched": sorted(touched),
                   "reachability_band": reach,
                   "incident_evidence": incident_hit},
        )

    # ------------------------------------------------------------------
    # 3. Engineering leverage
    # ------------------------------------------------------------------
    def engineering_leverage(self, episode: Mapping[str, Any]) -> Assessment:
        eid = str(episode["episode_id"])
        propagation = self.propagation.get(eid) or {}
        counter = list(episode.get("counterevidence") or [])
        evidence: list[dict[str, Any]] = []
        classes: list[str] = []
        thresholds = self.rubric["dimensions"]["engineering_leverage"]["thresholds"]

        if propagation.get("reason") and "graph-resolvable" in str(propagation["reason"]):
            confidence, creasons = self._confidence(episode)
            return _assess(
                episode_id=eid, dimension="engineering_leverage", band=None,
                rationale=(
                    "Leverage cannot be assessed: nothing this episode touched is "
                    "resolvable in the import graph."
                ),
                evidence=[], artifact_classes=[], counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_assessable",
                unknown_reason=str(propagation["reason"]),
            )

        files_reached = int(propagation.get("reach_file_count") or 0)
        components = int(propagation.get("distinct_component_penetration") or 0)
        authors = int(propagation.get("distinct_downstream_authors") or 0)
        depth = int(propagation.get("max_path_depth") or 0)

        if files_reached:
            classes.append("propagation_edge")
            evidence.append(
                {
                    "kind": "propagation_edge",
                    "detail": (
                        f"{files_reached} later file(s) in {components} component(s) "
                        f"by {authors} distinct author(s) depend on what this "
                        f"introduced (max depth {depth})"
                    ),
                }
            )
        novelty = self.novelty.get(eid) or {}
        if novelty.get("markers"):
            classes.append("introduced_module")
            evidence.append(
                {"kind": "introduced_module",
                 "detail": f"introduces {sorted(novelty['markers'])}"}
            )
        shared_library = any(
            (self.change_shape.get(int(n)) or {}).get("touches_shared_library")
            for n in episode.get("pr_numbers") or []
        )
        if shared_library:
            classes.append("shared_library")
            evidence.append(
                {"kind": "shared_library", "detail": "changes a shared library surface"}
            )
        if episode.get("doc_file_count"):
            classes.append("docs_or_changelog")

        if components >= int(thresholds["band_4_min_downstream_components"]) and \
                authors >= int(thresholds["band_4_min_downstream_authors"]):
            band = 4
            rationale = (
                f"Foundational: adopted across {components} components by {authors} "
                f"distinct engineers."
            )
        elif components >= int(thresholds["band_3_min_downstream_components"]) and \
                authors >= int(thresholds["band_3_min_downstream_authors"]):
            band = 3
            rationale = (
                f"Adopted beyond its own area: {components} components, {authors} "
                "distinct downstream authors."
            )
        elif files_reached >= int(thresholds["band_2_min_downstream_files"]) or shared_library:
            band = 2
            rationale = (
                f"Reusable beyond the immediate change: {files_reached} later file(s) "
                "depend on it" + (", and it touches a shared library" if shared_library else "")
                + "."
            )
        elif files_reached:
            band = 1
            rationale = "Adopted only within its immediate area."
        else:
            band = 0
            rationale = (
                "No later change depends on what this episode introduced, within "
                "the observed window."
            )
            evidence.append(
                {"kind": "no_adoption",
                 "detail": str(propagation.get("reason")
                               or "no downstream adoption observed in the window")}
            )

        confidence, creasons = self._confidence(episode)
        persistent = bool(propagation.get("persistence_detected"))
        if band >= 4 and not persistent:
            band = 3
            creasons.append(
                "capped at 3: band 4 requires sustained adoption near the window "
                "end (persistence), which was not observed"
            )
        band, caps = self._cap_band(band, classes, confidence, bool(novelty.get("markers")))
        creasons.extend(caps)

        return _assess(
            episode_id=eid, dimension="engineering_leverage", band=band,
            rationale=rationale, evidence=evidence, artifact_classes=classes,
            counterevidence=counter, confidence=confidence,
            confidence_reasons=creasons,
            corroboration_status=(
                "corroborated" if len(set(classes)) >= 2
                else "single_source" if classes else "uncorroborated"
            ),
            extra={
                "downstream_files": files_reached,
                "downstream_components": components,
                "downstream_authors": authors,
                "max_path_depth": depth,
                "hub_cap_applied": bool(propagation.get("cap_applied")),
            },
        )

    # ------------------------------------------------------------------
    # 4. Decision quality
    # ------------------------------------------------------------------
    def decision_quality(self, episode: Mapping[str, Any]) -> Assessment:
        eid = str(episode["episode_id"])
        numbers = [int(n) for n in episode.get("pr_numbers") or []]
        counter = list(episode.get("counterevidence") or [])
        evidence: list[dict[str, Any]] = []
        classes: list[str] = []
        rules = self.rubric["dimensions"]["decision_quality"]

        # -- review-driven redesign (the strongest before/after available) --
        design_interventions = [
            i for i in self.interventions.get(eid, [])
            if set(i.get("consequential_classes") or [])
            & {"design_architecture", "alternative_approach", "scope"}
            and i.get("consequence_band") in {"design_change", "local_change", "prevented_risk"}
        ]
        strong_design = [
            i for i in design_interventions
            if i.get("causal_confidence") in {"high", "medium"}
        ]
        if strong_design:
            classes.append("review_thread")
            evidence.append(
                {
                    "kind": "review_thread",
                    "url": strong_design[0].get("url"),
                    "detail": (
                        f"{len(strong_design)} review comment(s) raised a design, "
                        f"scope or alternative-approach concern and the code changed "
                        f"afterwards (consequence: "
                        f"{strong_design[0].get('consequence_band')})"
                    ),
                }
            )

        # -- simplification / descope -------------------------------------
        novelty = self.novelty.get(eid) or {}
        if novelty.get("is_simplification"):
            classes.append("simplification_diff")
            evidence.append(
                {"kind": "simplification_diff", "detail": str(novelty.get("rationale"))}
            )
        superseded = str(episode.get("status")) == "superseded"
        if superseded:
            classes.append("superseded_edge")
            evidence.append(
                {"kind": "superseded_edge",
                 "detail": "the approach was explicitly superseded by later work"}
            )

        # -- documented rationale in the PR body ---------------------------
        rationale_hits: list[str] = []
        for number in numbers:
            body = str((self.prs.get(number) or {}).get("body_text") or "").lower()
            for marker in rules["rationale_markers"]:
                if str(marker).lower() in body:
                    rationale_hits.append(f"PR #{number} body contains '{marker}'")
                    break
        if rationale_hits:
            classes.append("pr_body_rationale")
            evidence.append(
                {"kind": "pr_body_rationale", "detail": rationale_hits[0],
                 "artifact_id": pr_artifact(numbers[0]) if numbers else None}
            )

        redirection = [
            f"PR #{n} body contains '{marker}'"
            for n in numbers
            for marker in rules["redirection_markers"]
            if str(marker).lower() in str((self.prs.get(n) or {}).get("body_text") or "").lower()
        ]

        # Before/after is required. Without it the band is 0, not a guess.
        has_before_after = bool(strong_design or novelty.get("is_simplification") or superseded)
        if not has_before_after and not rationale_hits:
            confidence, creasons = self._confidence(episode)
            return _assess(
                episode_id=eid, dimension="decision_quality", band=0,
                rationale=(
                    "No observable before/after decision evidence: no review-driven "
                    "change of approach, no simplification, no superseded "
                    "alternative, no documented rationale."
                ),
                evidence=evidence, artifact_classes=classes, counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_required",
                extra={"requires_before_after": True, "before_after_found": False},
            )

        multi_component = len(
            {str(i.get("component")) for i in strong_design if i.get("component")}
        ) > 1
        if strong_design and (multi_component or any(
            i.get("consequence_band") == "design_change" for i in strong_design
        )):
            band = 3
            rationale = (
                "A review intervention materially changed the approach across "
                "multiple files or components, and was acknowledged or resolved."
            )
        elif has_before_after:
            band = 2
            rationale = (
                "Clear simplification, descope or accepted design correction with "
                "observable before/after evidence."
            )
        else:
            band = 1
            rationale = (
                "A documented implementation choice with stated rationale, but no "
                "observable change of direction."
            )

        confidence, creasons = self._confidence(episode)
        marker_present = bool(redirection)
        if marker_present and band >= 3:
            band = 4
            rationale += f" Explicit redirection language: {redirection[0]}."
        band, caps = self._cap_band(band, classes, confidence, marker_present)
        creasons.extend(caps)

        return _assess(
            episode_id=eid, dimension="decision_quality", band=band,
            rationale=rationale, evidence=evidence, artifact_classes=classes,
            counterevidence=counter, confidence=confidence,
            confidence_reasons=creasons,
            corroboration_status=(
                "corroborated" if len(set(classes)) >= 2
                else "single_source" if classes else "uncorroborated"
            ),
            extra={"requires_before_after": True, "before_after_found": has_before_after,
                   "design_interventions": len(strong_design)},
        )

    # ------------------------------------------------------------------
    # 5. Propagation / durability
    # ------------------------------------------------------------------
    def propagation_durability(self, episode: Mapping[str, Any]) -> Assessment:
        eid = str(episode["episode_id"])
        numbers = [int(n) for n in episode.get("pr_numbers") or []]
        propagation = self.propagation.get(eid) or {}
        corrective = self.corrective.get(eid) or {}
        counter = list(episode.get("counterevidence") or [])
        evidence: list[dict[str, Any]] = []
        classes: list[str] = []
        thresholds = self.rubric["dimensions"]["propagation_durability"]["thresholds"]
        reversion = self.rubric["dimensions"]["propagation_durability"]["reversion"]

        survivals = [
            (self.regression.get(n) or {}).get("survival_30d") for n in numbers
        ]
        measured = [s for s in survivals if s is not None]
        introduced = sum(
            int((self.regression.get(n) or {}).get("files_introduced") or 0)
            for n in numbers
        )
        unmeasurable_reasons = sorted(
            {
                str((self.regression.get(n) or {}).get("survival_30d_reason"))
                for n in numbers
                if (self.regression.get(n) or {}).get("survival_30d") is None
                and (self.regression.get(n) or {}).get("survival_30d_reason")
            }
        )

        components = int(propagation.get("distinct_component_penetration") or 0)
        depth = int(propagation.get("max_path_depth") or 0)
        persistent = bool(propagation.get("persistence_detected"))

        if measured:
            classes.append("survival_measurement")
            evidence.append(
                {
                    "kind": "survival_measurement",
                    "detail": f"mean 30-day survival of introduced files: "
                              f"{sum(measured) / len(measured):.2f} over "
                              f"{introduced} introduced file(s)",
                }
            )
        if components:
            classes.append("propagation_edge")
            evidence.append(
                {"kind": "propagation_edge",
                 "detail": f"downstream reuse across {components} component(s), "
                           f"max depth {depth}"}
            )
        if persistent:
            classes.append("persistence_evidence")
            evidence.append(
                {
                    "kind": "persistence_evidence",
                    "detail": (
                        f"{propagation.get('persistence_events_in_window')} adoption "
                        f"event(s) within {self.config.get('analytics.decay.persistence_window_days')} "
                        f"days of the window end; survival floor "
                        f"{propagation.get('survival_floor')} applied instead of raw "
                        f"decay {propagation.get('raw_decay_factor')}"
                    ),
                }
            )
        if int(episode.get("pr_count") or 0) > 1:
            classes.append("follow_up_pr")

        # Nothing to measure at all -> unknown, not zero.
        if not measured and not components and introduced == 0:
            confidence, creasons = self._confidence(episode)
            reason = (
                "; ".join(unmeasurable_reasons)
                or "the episode introduced no files and nothing downstream depends on it"
            )
            band = None if unmeasurable_reasons else 0
            return _assess(
                episode_id=eid, dimension="propagation_durability", band=band,
                rationale=(
                    "Durability could not be measured." if band is None
                    else "No durability evidence: nothing introduced, nothing adopted."
                ),
                evidence=evidence, artifact_classes=classes, counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_assessable" if band is None else "not_required",
                unknown_reason=reason if band is None else None,
            )

        if depth >= int(thresholds["band_4_min_path_depth"]) and persistent:
            band = 4
            rationale = (
                f"Multi-generation enablement: adoption chains reach depth {depth} "
                "and are still active near the window end."
            )
        elif components >= int(thresholds["band_3_min_downstream_components"]):
            band = 3
            rationale = (
                f"Demonstrable downstream reuse across {components} component "
                "boundaries."
            )
        elif measured and sum(measured) / len(measured) >= 0.99 and int(
            episode.get("pr_count") or 0
        ) > 1:
            band = 2
            rationale = (
                "Introduced files survive and the work received normal follow-up."
            )
        elif measured and sum(measured) / len(measured) >= 0.99:
            band = 1
            rationale = "Introduced files survive locally to the last observable checkpoint."
        else:
            band = 1
            rationale = "Some durability evidence, confined to the immediate area."

        confidence, creasons = self._confidence(episode)

        # Corrective burden: capped, applied here and nowhere else.
        penalty = float(corrective.get("capped_penalty") or 0.0)
        if corrective.get("confirmed_revert"):
            cap = int(reversion["confirmed_revert_caps_band_at"])
            if band > cap:
                band = cap
                creasons.append(
                    f"capped at {cap}: an explicit, un-reapplied revert is confirmed "
                    "counterevidence"
                )
        elif penalty > 0:
            reduced = max(0, band - int(round(penalty)))
            if reduced != band:
                creasons.append(
                    f"reduced from {band} to {reduced} by capped corrective burden "
                    f"{penalty} ({corrective.get('by_class')})"
                )
                band = reduced

        band, caps = self._cap_band(band, classes, confidence, persistent and depth >= 2)
        creasons.extend(caps)

        if unmeasurable_reasons:
            creasons.append(
                "survival is unmeasurable for part of this episode: "
                + "; ".join(unmeasurable_reasons[:2])
            )

        return _assess(
            episode_id=eid, dimension="propagation_durability", band=band,
            rationale=rationale, evidence=evidence, artifact_classes=classes,
            counterevidence=counter + [
                {"kind": "corrective_burden", "evidence_tier": "mixed",
                 "detail": f"corrective events: {corrective.get('by_class')}, "
                           f"capped penalty {penalty}"}
            ] if penalty or corrective.get("confirmed_revert") else counter,
            confidence=confidence, confidence_reasons=creasons,
            corroboration_status=(
                "corroborated" if len(set(classes)) >= 2
                else "single_source" if classes else "uncorroborated"
            ),
            extra={
                "mean_survival_30d": (
                    round(sum(measured) / len(measured), 4) if measured else None
                ),
                "survival_unmeasurable_reasons": unmeasurable_reasons,
                "downstream_components": components,
                "max_path_depth": depth,
                "persistence_detected": persistent,
                "corrective_penalty_applied": penalty,
            },
        )

    # ------------------------------------------------------------------
    # 6. Collaborative amplification
    # ------------------------------------------------------------------
    def collaborative_amplification(self, episode: Mapping[str, Any]) -> Assessment:
        """Driven only by causally-confirmed interventions — never by counts.

        This evaluator deliberately never reads ``review_count``,
        ``comment_count`` or ``substantive_comments``. Its whole input is the
        set of interventions that passed causal assessment, and the thresholds
        below are on *distinct authors helped* and *distinct components*, which
        are breadth-of-effect statements.
        """
        eid = str(episode["episode_id"])
        counter = list(episode.get("counterevidence") or [])
        evidence: list[dict[str, Any]] = []
        classes: list[str] = []
        thresholds = self.rubric["dimensions"]["collaborative_amplification"]["thresholds"]

        interventions = [
            i for i in self.interventions.get(eid, []) if i.get("is_consequential")
        ]
        confirmed = [
            i for i in interventions if i.get("causal_confidence") in {"high", "medium"}
        ]
        safety = [
            i for i in confirmed
            if i.get("consequence_band") == "prevented_risk"
        ]
        authors_helped = {
            str(i.get("pr_author_actor_id")) for i in confirmed if i.get("pr_author_actor_id")
        }
        components = {str(i.get("component")) for i in confirmed if i.get("component")}

        if confirmed:
            classes.append("causal_review_intervention")
            evidence.append(
                {
                    "kind": "causal_review_intervention",
                    "url": confirmed[0].get("url"),
                    "detail": (
                        f"{len(confirmed)} causally-confirmed intervention(s) helping "
                        f"{len(authors_helped)} distinct author(s) across "
                        f"{len(components)} component(s)"
                    ),
                }
            )
        if safety:
            classes.append("safety_intervention")
            evidence.append(
                {
                    "kind": "safety_intervention",
                    "url": safety[0].get("url"),
                    "detail": f"{len(safety)} intervention(s) raised a safety concern "
                              "and the code changed afterwards",
                }
            )
        if episode.get("doc_file_count"):
            classes.append("docs_or_changelog")
            evidence.append(
                {"kind": "docs_or_changelog",
                 "detail": f"{episode['doc_file_count']} documentation file(s) changed"}
            )
        if episode.get("has_ai_co_author") is not None and int(
            episode.get("commit_count") or 0
        ) > 0:
            # Human co-authorship, not AI assistance, is the collaboration signal.
            pass
        if episode.get("issue_numbers"):
            classes.append("issue_triage")

        if not confirmed and not episode.get("doc_file_count"):
            resolved_threads = [
                i for i in self.interventions.get(eid, []) if i.get("thread_is_resolved")
            ]
            confidence, creasons = self._confidence(episode)
            if resolved_threads:
                return _assess(
                    episode_id=eid, dimension="collaborative_amplification", band=1,
                    rationale=(
                        "Helpful local collaboration: substantive review threads were "
                        "opened and resolved, but no change could be attributed to them."
                    ),
                    evidence=[{"kind": "resolved_thread",
                               "url": resolved_threads[0].get("url"),
                               "detail": f"{len(resolved_threads)} resolved review thread(s)"}],
                    artifact_classes=["causal_review_intervention"],
                    counterevidence=counter, confidence=confidence,
                    confidence_reasons=creasons, corroboration_status="single_source",
                )
            return _assess(
                episode_id=eid, dimension="collaborative_amplification", band=0,
                rationale=(
                    "No collaborative evidence: no causally-confirmed review "
                    "intervention and no knowledge artifact."
                ),
                evidence=evidence, artifact_classes=classes, counterevidence=counter,
                confidence=confidence, confidence_reasons=creasons,
                corroboration_status="not_required",
            )

        if len(authors_helped) >= int(thresholds["band_4_min_distinct_authors_helped"]) \
                and len(components) >= int(thresholds["band_4_min_distinct_components"]):
            band = 4
            rationale = (
                f"Organization-scale amplification: {len(authors_helped)} distinct "
                f"engineers helped across {len(components)} components with "
                "confirmed consequence."
            )
        elif (len(authors_helped) >= int(thresholds["band_3_min_distinct_authors_helped"])
              and len(components) >= int(thresholds["band_3_min_distinct_components"])) \
                or safety:
            band = 3
            rationale = (
                f"Enables several engineers or prevented a major error: "
                f"{len(authors_helped)} author(s), {len(components)} component(s)"
                + (f", including {len(safety)} prevented-risk intervention(s)" if safety else "")
                + "."
            )
        elif confirmed or episode.get("doc_file_count"):
            band = 2
            rationale = (
                "Material unblock: a review intervention changed the code, or a "
                "knowledge artifact was contributed."
            )
        else:
            band = 1
            rationale = "Helpful local collaboration."

        confidence, creasons = self._confidence(episode)
        band, caps = self._cap_band(band, classes, confidence, bool(safety) and len(safety) > 1)
        creasons.extend(caps)

        return _assess(
            episode_id=eid, dimension="collaborative_amplification", band=band,
            rationale=rationale, evidence=evidence, artifact_classes=classes,
            counterevidence=counter, confidence=confidence,
            confidence_reasons=creasons,
            corroboration_status=(
                "corroborated" if len(set(classes)) >= 2
                else "single_source" if classes else "uncorroborated"
            ),
            extra={
                "confirmed_interventions": len(confirmed),
                "distinct_authors_helped": len(authors_helped),
                "distinct_components": len(components),
                "prevented_risk_interventions": len(safety),
                "inputs_exclude_counts": True,
            },
        )

    # ------------------------------------------------------------------
    def evaluate(self, episode: Mapping[str, Any]) -> list[Assessment]:
        return [
            self.product_outcome(episode),
            self.reliability_risk(episode),
            self.engineering_leverage(episode),
            self.decision_quality(episode),
            self.propagation_durability(episode),
            self.collaborative_amplification(episode),
        ]

    def evaluate_all(self, episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            rows.extend(self.evaluate(episode))
        log.info(
            "dimensions: %d assessments over %d episodes", len(rows), len(episodes)
        )
        return rows


def summarise(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    distribution: dict[str, dict[str, int]] = {
        d: defaultdict(int) for d in DIMENSIONS
    }
    confidence: dict[str, int] = defaultdict(int)
    for row in items:
        key = "unknown" if row.get("band") is None else str(row["band"])
        distribution[str(row["dimension"])][key] += 1
        confidence[str(row.get("confidence"))] += 1
    return {
        "assessments": len(items),
        "band_distribution": {
            d: dict(sorted(counts.items())) for d, counts in distribution.items()
        },
        "confidence_distribution": dict(sorted(confidence.items())),
        "unknown_rate": round(
            sum(1 for r in items if r.get("band") is None) / (len(items) or 1), 4
        ),
        "band_4_count": sum(1 for r in items if r.get("band") == 4),
        "band_3_count": sum(1 for r in items if r.get("band") == 3),
        "with_counterevidence": sum(1 for r in items if r.get("counterevidence")),
        "rubric_version": VERSION,
    }
