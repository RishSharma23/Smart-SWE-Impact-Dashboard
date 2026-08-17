"""The static export package Phase 3 consumes.

Eight JSON files, no live model, no database, no server-side computation.  The
UI is a static site over these files; every number it can show is already
computed and every sentence it can print is already a claim with evidence
behind it.

Contract properties Phase 3 may rely on:

* **Self-describing.**  ``dashboard_manifest.json`` lists every file, its
  content hash, its row count and the methodology version that produced it.
  Read it first.
* **Precomputed indexes.**  Filtering by scenario, component, dimension or
  engineer never requires a scan: ``indexes`` in the manifest holds the
  inverted lists.
* **Sharded evidence.**  ``evidence/`` is split by artifact kind so a page
  showing one episode does not download every review comment in the dataset.
* **No orphan prose.**  Every human-readable sentence in ``episodes.json``,
  ``engineers.json`` and ``comparisons.json`` is a ``claim_id`` resolvable in
  ``claims.json``.  Rendering a string that is not a claim is a contract
  violation on the UI side.
* **Nothing sensitive.**  No API keys, no local paths, no private email, no raw
  model reasoning. A gate greps for all four before writing.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import Phase2Config, iso, now
from .ids import content_hash, sha256_file
from .store import write_json, write_json_compact
from .versions import EXPORT_SCHEMA_VERSION, METHODOLOGY_VERSION, all_versions

log = logging.getLogger("impact2.export")

VERSION = EXPORT_SCHEMA_VERSION

# Things that must never reach a published file.
#
# Precision matters here as much as recall. The first version of this scan
# reported 29 violations that were all false positives — npm `package@1.2.3`
# specifiers read as emails, the literal string `/Users/.../` from PostHog's own
# source read as a local path, and the words "gender"/"seniority" flagged inside
# methodology.json, where they appear *because* the config documents them as
# things this system never infers. A gate that cries wolf gets switched off, and
# it was masking the four real publication blockers.
FORBIDDEN_PATTERNS = (
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "github token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "github fine-grained token"),
    (re.compile(r"\bsk-(?:or-)?[A-Za-z0-9-]{20,}"), "provider api key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws key"),
    # A real home directory has a real username. `/Users/.../` is a placeholder
    # that appears in the analysed repository's own code.
    (re.compile(r"/(?:Users|home)/(?!\.\.\./)[A-Za-z0-9._-]{2,}/"), "local filesystem path"),
    # An email, but not a package specifier (`posthog-js@1.404.1`,
    # `kea@4.0.0-pre.6.patch`, `mcp@0.2.0`) — the local part of those is a
    # package name and the domain is a semver string.
    (
        re.compile(
            r"\b[\w.+-]+@(?!\d)[\w-]+\.(?!\d)[A-Za-z][\w.-]*[A-Za-z]\b"
        ),
        "email address",
    ),
)

# Files whose *content* is analysis output, so a forbidden inference field name
# appearing in them would mean the system actually inferred it. methodology.json
# is excluded deliberately: it lists those words to state they are never used.
INFERENCE_SCANNED_FILES = ("episodes.json", "engineers.json", "claims.json",
                           "comparisons.json", "rankings.json")

# Same shape as the detector above, used to redact rather than merely report.
EMAIL_IN_TEXT_RE = re.compile(
    r"\b[\w.+-]+@(?!\d)[\w-]+\.(?!\d)[A-Za-z][\w.-]*[A-Za-z]\b"
)

# Field names the eligibility config forbids; greped for by the same gate.
def _forbidden_fields(config: Phase2Config) -> list[str]:
    return [str(f) for f in config.get("eligibility.forbidden_inferences")]


def _public_engineer(portfolio: Mapping[str, Any]) -> dict[str, Any]:
    """Profile-safe fields only. A login is public; an email is not."""
    return {
        "actor_cluster_id": portfolio.get("actor_cluster_id"),
        "login": portfolio.get("login"),
        "display_name": portfolio.get("display_name"),
        "profile_url": (
            f"https://github.com/{portfolio['login']}" if portfolio.get("login") else None
        ),
        "avatar_url": (
            f"https://github.com/{portfolio['login']}.png" if portfolio.get("login") else None
        ),
        "affiliation": portfolio.get("affiliation"),
        "affiliation_note": portfolio.get("affiliation_note"),
        "identity_ambiguity": portfolio.get("identity_ambiguity"),
        "identity_ambiguity_reasons": portfolio.get("identity_ambiguity_reasons"),
    }


class Exporter:
    def __init__(
        self,
        config: Phase2Config,
        pipeline: Any,
        *,
        claims: Sequence[Mapping[str, Any]],
        claim_index: Mapping[str, Any],
        validation: Mapping[str, Any],
        sensitivity: Mapping[str, Any],
        llm_report: Mapping[str, Any],
        llm_pending: Mapping[str, Any],
    ) -> None:
        self.config = config
        self.pipeline = pipeline
        self.claims = list(claims)
        self.claim_index = dict(claim_index)
        self.validation = dict(validation)
        self.sensitivity = dict(sensitivity)
        self.llm_report = dict(llm_report)
        self.llm_pending = dict(llm_pending)
        self.out = config.paths.export
        self.files: dict[str, dict[str, Any]] = {}

    # -- writing -----------------------------------------------------------
    @staticmethod
    def _redact(payload: Any) -> Any:
        """Strip email addresses from every string before it is published.

        Excerpts are quoted verbatim from public PR and commit text so a reader
        can check a claim, and that text sometimes contains an address. Public
        is not the same as fair to republish on a dashboard about named people,
        so the address goes and the sentence stays readable.
        """
        if isinstance(payload, Mapping):
            return {k: Exporter._redact(v) for k, v in payload.items()}
        if isinstance(payload, (list, tuple)):
            return [Exporter._redact(v) for v in payload]
        if isinstance(payload, str) and "@" in payload:
            return EMAIL_IN_TEXT_RE.sub("[email redacted]", payload)
        return payload

    def _write(self, name: str, payload: Any, *, rows: int | None = None) -> None:
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_compact(path, self._redact(payload))
        self.files[name] = {
            "path": name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "rows": rows,
        }

    # -- 1. episodes --------------------------------------------------------
    def episodes(self) -> list[dict[str, Any]]:
        dims_by_episode: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in self.pipeline.dimensions:
            dims_by_episode[str(row["episode_id"])].append(row)
        participants_by_episode: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in self.pipeline.participants:
            participants_by_episode[str(row["episode_id"])].append(row)
        artifacts_by_episode: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in self.pipeline.episode_artifacts:
            artifacts_by_episode[str(row["episode_id"])].append(row)
        propagation = {str(r["episode_id"]): r for r in self.pipeline.propagation_summary}
        novelty = {str(r["episode_id"]): r for r in self.pipeline.novelty}
        corrective = {str(r["episode_id"]): r for r in self.pipeline.corrective}

        rows: list[dict[str, Any]] = []
        for episode in self.pipeline.episodes:
            eid = str(episode["episode_id"])
            claim_ids = self.claim_index.get("episodes", {}).get(eid, {})
            rows.append(
                {
                    "episode_id": eid,
                    # Narrative is claim-addressed, never free text.
                    "title_claim_id": claim_ids.get("title"),
                    "problem_claim_id": claim_ids.get("problem"),
                    "intervention_claim_id": claim_ids.get("intervention"),
                    "outcome_claim_id": claim_ids.get("observable_outcome"),
                    "title": episode.get("title"),
                    "started_at": episode.get("started_at"),
                    "ended_at": episode.get("ended_at"),
                    "duration_days": episode.get("duration_days"),
                    "status": episode.get("status"),
                    "status_reasons": episode.get("status_reasons"),
                    "release_corroboration": episode.get("release_corroboration"),
                    "release_evidence": episode.get("release_evidence"),
                    "components": episode.get("components"),
                    "products": episode.get("products"),
                    "reachability_band": episode.get("reachability_band"),
                    "feature_flag_keys": episode.get("feature_flag_keys"),
                    "pr_numbers": episode.get("pr_numbers"),
                    "issue_numbers": episode.get("issue_numbers"),
                    "cluster_confidence": episode.get("cluster_confidence"),
                    "cluster_confidence_reasons": episode.get("cluster_confidence_reasons"),
                    "sub_episode_links": episode.get("sub_episode_links"),
                    "counterevidence": episode.get("counterevidence"),
                    "has_ai_co_author": episode.get("has_ai_co_author"),
                    "touches_enterprise_licensed_code": episode.get(
                        "touches_enterprise_licensed_code"
                    ),
                    "dimensions": [
                        {
                            "dimension": d["dimension"],
                            "band": d.get("band"),
                            "band_label": d.get("band_label"),
                            "is_unknown": d.get("is_unknown"),
                            "unknown_reason": d.get("unknown_reason"),
                            "confidence": d.get("confidence"),
                            "confidence_reasons": d.get("confidence_reasons"),
                            "corroboration_status": d.get("corroboration_status"),
                            "artifact_classes": d.get("artifact_classes"),
                            "evidence": d.get("evidence"),
                            "counterevidence": d.get("counterevidence"),
                            "rationale_claim_id": self.claim_index.get(
                                "dimensions", {}
                            ).get(str(d["dimension_record_id"])),
                        }
                        for d in sorted(dims_by_episode.get(eid, []),
                                        key=lambda r: str(r["dimension"]))
                    ],
                    "participants": [
                        {
                            "actor_cluster_id": p.get("actor_cluster_id"),
                            "login": p.get("login"),
                            "roles": p.get("roles"),
                            "share_category": p.get("share_category"),
                            "share_reasons": p.get("share_reasons"),
                            "attribution_confidence": p.get("attribution_confidence"),
                            "direct_evidence": p.get("direct_evidence"),
                            "claim_ids": self.claim_index.get("participants", {}).get(
                                str(p["participant_id"]), []
                            ),
                        }
                        for p in sorted(participants_by_episode.get(eid, []),
                                        key=lambda r: str(r.get("login") or ""))
                        if p.get("has_any_evidence")
                    ],
                    "artifact_ids": [
                        a["artifact_id"] for a in artifacts_by_episode.get(eid, [])
                    ],
                    "analytics": {
                        "propagation": _slim_propagation(propagation.get(eid) or {}),
                        "novelty": {
                            "novelty_class": (novelty.get(eid) or {}).get("novelty_class"),
                            "rationale": (novelty.get(eid) or {}).get("rationale"),
                            "markers": (novelty.get(eid) or {}).get("markers"),
                            "uncertainty": (novelty.get(eid) or {}).get("uncertainty"),
                        },
                        "corrective_burden": {
                            "by_class": (corrective.get(eid) or {}).get("by_class"),
                            "capped_penalty": (corrective.get(eid) or {}).get("capped_penalty"),
                            "confirmed_revert": (corrective.get(eid) or {}).get("confirmed_revert"),
                            "unconfirmed_event_count": (
                                corrective.get(eid) or {}
                            ).get("unconfirmed_event_count"),
                        },
                    },
                }
            )
        self._write("episodes.json", rows, rows=len(rows))
        return rows

    # -- 2. engineers --------------------------------------------------------
    def engineers(self) -> list[dict[str, Any]]:
        stability = {
            str(e["actor_cluster_id"]): e
            for e in (self.sensitivity.get("engineers") or [])
        }
        rows: list[dict[str, Any]] = []
        for portfolio in self.pipeline.portfolios:
            actor = str(portfolio["actor_cluster_id"])
            rows.append(
                {
                    **_public_engineer(portfolio),
                    "portfolio_id": portfolio.get("portfolio_id"),
                    "thesis_claim_ids": self.claim_index.get("portfolios", {}).get(actor, []),
                    "dimension_profile": [
                        {
                            "dimension": dimension,
                            "value": (portfolio.get("dimension_values") or {}).get(dimension),
                            "interval": (portfolio.get("dimension_intervals") or {}).get(dimension),
                            "confidence": (portfolio.get("dimension_confidence") or {}).get(dimension),
                            "is_unknown": dimension in (portfolio.get("unknown_dimensions") or []),
                            "unknown_reason": (
                                (portfolio.get("dimension_detail") or {})
                                .get(dimension, {})
                                .get("unknown_reason")
                            ),
                            "top_episode_id": (
                                (portfolio.get("dimension_detail") or {})
                                .get(dimension, {})
                                .get("top_episode_id")
                            ),
                            "episode_count": (
                                (portfolio.get("dimension_detail") or {})
                                .get(dimension, {})
                                .get("episode_count")
                            ),
                            "aggregation_trace": (
                                (portfolio.get("dimension_detail") or {})
                                .get(dimension, {})
                                .get("aggregation_trace")
                            ),
                        }
                        for dimension in (portfolio.get("dimension_values") or {})
                    ],
                    "strongest_dimension": portfolio.get("strongest_dimension"),
                    "strongest_evidence_episode_id": portfolio.get(
                        "strongest_evidence_episode_id"
                    ),
                    "episode_ids": portfolio.get("episode_ids"),
                    "episode_count": portfolio.get("episode_count"),
                    "current_episode_ids": portfolio.get("current_episode_ids"),
                    "foundational_episode_ids": portfolio.get("foundational_episode_ids"),
                    "roles_held": portfolio.get("roles_held"),
                    "concentration_profile": portfolio.get("concentration_profile"),
                    "diversity_affects_ranking": False,
                    "active_period": portfolio.get("active_period"),
                    "rankable": portfolio.get("rankable"),
                    "eligibility_label": portfolio.get("eligibility_label"),
                    "eligibility_reasons": portfolio.get("eligibility_reasons"),
                    "uncertainty": {
                        "rank_stability_index": (stability.get(actor) or {}).get(
                            "rank_stability_index"
                        ),
                        "top5_inclusion_probability": (stability.get(actor) or {}).get(
                            "mean_top5_inclusion_probability"
                        ),
                        "position_range": (stability.get(actor) or {}).get("position_range"),
                        "claim_id": self.claim_index.get("stability", {}).get(actor),
                    },
                }
            )
        rows.sort(key=lambda r: (not r["rankable"], str(r.get("login") or "")))
        self._write("engineers.json", rows, rows=len(rows))
        return rows

    # -- 3. rankings ----------------------------------------------------------
    def rankings(self) -> dict[str, Any]:
        scenarios = {s["scenario"]: s for s in self.pipeline.scenarios}
        stability = {
            str(e["actor_cluster_id"]): e
            for e in (self.sensitivity.get("engineers") or [])
        }
        payload = {
            "default_scenario": "balanced",
            "scenarios": [
                {
                    "scenario": run["scenario"],
                    "label": scenarios.get(run["scenario"], {}).get("label"),
                    "description": scenarios.get(run["scenario"], {}).get("description"),
                    "available": run.get("available", False),
                    "unavailable_reason": run.get("unavailable_reason"),
                    "remedy": run.get("remedy"),
                    "note": scenarios.get(run["scenario"], {}).get("note"),
                    "weights": run.get("weights"),
                    "thresholds": run.get("thresholds"),
                    "alternatives": run.get("alternatives"),
                    "excluded_insufficient_evidence": run.get(
                        "excluded_insufficient_evidence"
                    ),
                    "positions": [
                        {
                            "position": r["position"],
                            "tier": r["tier"],
                            "actor_cluster_id": r["actor_cluster_id"],
                            "login": r.get("login"),
                            "dimension_values": r.get("dimension_values"),
                            "incomparable_with": r.get("incomparable_with"),
                            "incomparable_count": r.get("incomparable_count"),
                            "cross_check_position": r.get("cross_check_position"),
                            "cross_check_delta": r.get("cross_check_delta"),
                            "stability": {
                                "rank_stability_index": (
                                    stability.get(str(r["actor_cluster_id"])) or {}
                                ).get("rank_stability_index"),
                                "top5_inclusion_probability": (
                                    stability.get(str(r["actor_cluster_id"])) or {}
                                ).get("mean_top5_inclusion_probability"),
                                "position_range": (
                                    stability.get(str(r["actor_cluster_id"])) or {}
                                ).get("position_range"),
                            },
                        }
                        for r in run.get("ranking") or []
                    ],
                    "cross_check": {
                        k: v for k, v in (run.get("cross_check") or {}).items()
                        if k != "ranking"
                    },
                }
                for run in self.pipeline.ranking_runs
            ],
            "method": {
                "name": "ELECTRE III",
                "cross_check": "PROMETHEE II",
                "why_not_a_score": (
                    "A single number would have to encode an exchange rate "
                    "between shipping a product surface and preventing a "
                    "data-loss bug. There is no honest exchange rate, so "
                    "engineers are compared pairwise on six criteria and the "
                    "credibility of each comparison is published."
                ),
                "tiers_explained": (
                    "Engineers in the same tier are not distinguishable on this "
                    "evidence. Incomparability is a real result, not a tie-break "
                    "failure."
                ),
            },
        }
        self._write("rankings.json", payload)
        return payload

    # -- 4. comparisons --------------------------------------------------------
    def comparisons(self) -> dict[str, Any]:
        """Pairwise material for the top five of every available scenario."""
        payload: dict[str, Any] = {"scenarios": {}}
        for run in self.pipeline.ranking_runs:
            if not run.get("available"):
                continue
            top = [str(r["actor_cluster_id"]) for r in (run.get("ranking") or [])[:5]]
            top_set = set(top)
            relevant = [
                c for c in run.get("comparisons") or []
                if str(c.get("a")) in top_set and str(c.get("b")) in top_set
            ]
            payload["scenarios"][run["scenario"]] = {
                "top_five": top,
                "pairwise": [
                    {
                        "a": c["a"], "b": c["b"],
                        "a_login": c.get("a_login"), "b_login": c.get("b_login"),
                        "concordance": c.get("concordance"),
                        "credibility": c.get("credibility"),
                        "per_criterion": c.get("per_criterion"),
                        "excluded_criteria": c.get("excluded_criteria"),
                        "vetoing_criteria": c.get("vetoing_criteria"),
                        "counterevidence_veto": c.get("counterevidence_veto"),
                        "explanation_claim_id": self.claim_index.get(
                            "comparisons", {}
                        ).get(f"{run['scenario']}:{c['a']}vs{c['b']}"),
                    }
                    for c in relevant
                ],
                "methodology_trace": (
                    "Every pair above is published with its per-criterion "
                    "concordance, discordance, weights and thresholds. Excluded "
                    "criteria are unknown for one side and are not scored as zero."
                ),
            }
        self._write("comparisons.json", payload)
        return payload

    # -- 5. evidence (sharded) ---------------------------------------------------
    def evidence(self) -> dict[str, Any]:
        by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()
        for row in self.pipeline.episode_artifacts:
            kind = str(row["artifact_kind"])
            key = (kind, str(row["artifact_id"]))
            if key in seen:
                continue
            seen.add(key)
            by_kind[kind].append(
                {
                    "artifact_id": row["artifact_id"],
                    "kind": kind,
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "provenance": row.get("evidence_provenance"),
                    "detail": row.get("detail"),
                }
            )
        # Review comments carry the excerpt the claim rests on.
        for row in self.pipeline.interventions:
            key = ("review_comment", str(row["artifact_id"]))
            if key in seen:
                continue
            seen.add(key)
            by_kind["review_comment"].append(
                {
                    "artifact_id": row["artifact_id"],
                    "kind": "review_comment",
                    "title": str(row.get("body_excerpt") or "")[:120],
                    "url": row.get("url"),
                    "provenance": "deterministic:phase1.review_comments",
                    "detail": str(row.get("body_excerpt") or "")[:600],
                    "concern_classes": row.get("concern_classes"),
                    "consequence_band": row.get("consequence_band"),
                    "causal_confidence": row.get("causal_confidence"),
                    "change_evidence": row.get("change_evidence"),
                }
            )

        index: dict[str, Any] = {}
        for kind, rows in sorted(by_kind.items()):
            name = f"evidence/{kind}.json"
            rows.sort(key=lambda r: str(r["artifact_id"]))
            self._write(name, rows, rows=len(rows))
            index[kind] = {"file": name, "count": len(rows)}
        payload = {
            "sharded": True,
            "shards": index,
            "total_artifacts": sum(v["count"] for v in index.values()),
            "note": (
                "Sharded by artifact kind so an episode page does not download "
                "every review comment in the dataset. Excerpts are retained "
                "only where the text is the evidence a claim rests on."
            ),
        }
        self._write("evidence.json", payload)
        return payload

    # -- 6. claims ----------------------------------------------------------------
    def claims_file(self) -> dict[str, Any]:
        payload = {
            "claims": self.claims,
            "count": len(self.claims),
            "contract": (
                "Every human-readable sentence the UI renders must come from a "
                "claim in this file. A string that is not a claim_id lookup is a "
                "contract violation."
            ),
            "correction_pathway": self.config.get(
                "eligibility.limitations.correction_pathway"
            ),
        }
        self._write("claims.json", payload, rows=len(self.claims))
        return payload

    # -- 7. methodology ------------------------------------------------------------
    def methodology(self) -> dict[str, Any]:
        payload = {
            "methodology_version": METHODOLOGY_VERSION,
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "versions": all_versions(),
            "impact_definition": (
                "Observable engineering impact is a defensible change in product "
                "capability, user experience, system quality, organizational "
                "leverage, or future delivery capacity that can be materially "
                "attributed to an engineer's decisions and contributions using "
                "public evidence."
            ),
            "unit_of_analysis": "impact episode (a connected initiative arc), "
                                "not the commit or the pull request",
            "rubric": self.config.section("rubric"),
            "attribution": self.config.section("attribution"),
            "outranking": self.config.section("outranking"),
            "analytics": self.config.section("analytics"),
            "episode_construction": self.config.section("episodes"),
            "eligibility": self.config.section("eligibility"),
            "formulas": {
                "portfolio_aggregation": (
                    "value = min(scale_max, v1 + min(headroom, "
                    "sum(coeff_i * v_i for i >= 2))) where "
                    "v_i = band_i * confidence_discount_i * attribution_factor_i "
                    "* decay_i, ordered descending"
                ),
                "time_decay": "exp(-ln(2) * age_days / half_life_days)",
                "persistence_override": (
                    "effective_decay = max(raw_decay, survival_floor) when the "
                    "artifact is still being adopted within persistence_window_days "
                    "of the window end"
                ),
                "hub_damping": "path_weight = 1 / (1 + log2(1 + fan_in))",
                "edge_combination": (
                    "pair_weight = 1 - prod(1 - edge_strength_i)  (noisy-OR)"
                ),
                "concordance": (
                    "c_j(a,b) = 1 if g_j(a) >= g_j(b) - q; 0 if g_j(a) <= g_j(b) - p; "
                    "linear between"
                ),
                "discordance": (
                    "d_j(a,b) = 0 if g_j(b) <= g_j(a) + p; 1 if g_j(b) >= g_j(a) + v; "
                    "linear between"
                ),
                "credibility": (
                    "C(a,b) * prod over j where d_j > C of (1 - d_j) / (1 - C)"
                ),
            },
            "explicitly_not_used": [
                "commit count", "pull-request count", "lines of code",
                "review count", "velocity ratios", "any 0-1000 composite score",
                "per-day normalisation", "gradient-descent-learned weights",
            ],
            "llm": {
                **{k: v for k, v in self.llm_report.items() if k != "api_key"},
                "role": (
                    "Structured evidence extraction and summarisation only. The "
                    "LLM never produces the final ranking; deterministic bands "
                    "are authoritative and the agreement rate is reported."
                ),
                "pending_queue": {
                    k: v for k, v in self.llm_pending.items() if k != "items"
                },
            },
        }
        self._write("methodology.json", payload)
        return payload

    # -- 8. coverage --------------------------------------------------------------
    def coverage(self) -> dict[str, Any]:
        limitations = self.config.get("eligibility.limitations")
        payload = {
            "phase1": self.pipeline.inputs.verification_report(),
            "known_gaps": self.pipeline.inputs.known_gaps,
            "capabilities_disabled": self.pipeline.inputs.capabilities_disabled,
            "summaries": self.pipeline.summaries,
            "validation": {
                "status": self.validation.get("status"),
                "publishable": self.validation.get("publishable"),
                "publishable_blockers": self.validation.get("publishable_blockers"),
                # Client-facing summary only. The operator-facing detail —
                # queue file names, per-item notes, honesty caveats about how a
                # gate was satisfied — stays in reports/phase2/, which is not
                # published. A dashboard reader needs to know a check ran and
                # passed, not how the sausage was inspected.
                "items": [
                    {
                        "item": item.get("item"),
                        "description": item.get("description"),
                        "status": item.get("status"),
                    }
                    for item in (self.validation.get("items") or [])
                ],
            },
            "limitations": {
                "headline": limitations["headline"],
                "items": limitations["items"],
                "claim_ids": self.claim_index.get("limitations", []),
                "correction_pathway": limitations["correction_pathway"],
            },
            "missingness": self._missingness(),
        }
        self._write("coverage.json", payload)
        return payload

    def _missingness(self) -> dict[str, Any]:
        dims = self.pipeline.dimensions
        by_dimension: dict[str, dict[str, int]] = defaultdict(
            lambda: {"assessed": 0, "unknown": 0}
        )
        for row in dims:
            key = str(row["dimension"])
            by_dimension[key]["assessed"] += 1
            if row.get("band") is None:
                by_dimension[key]["unknown"] += 1
        return {
            "dimension_unknown_rates": {
                k: {
                    **v,
                    "unknown_rate": round(v["unknown"] / max(1, v["assessed"]), 4),
                }
                for k, v in sorted(by_dimension.items())
            },
            "episodes_without_diff": sum(
                1 for e in self.pipeline.episodes if not e.get("file_count")
            ),
            "episodes_without_release_corroboration": sum(
                1 for e in self.pipeline.episodes
                if e.get("release_corroboration") != "corroborated"
            ),
            "engineers_below_evidence_bar": sum(
                1 for p in self.pipeline.portfolios if not p.get("rankable")
            ),
            "note": (
                "Unknown is not zero anywhere in this package. An unknown "
                "dimension is excluded from pairwise comparison and widens the "
                "engineer's interval; it never lowers their position."
            ),
        }

    # -- manifest + indexes ---------------------------------------------------------
    def manifest(self, episodes: Sequence[Mapping[str, Any]],
                 engineers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        indexes = {
            "episodes_by_component": _invert(
                episodes, "episode_id", lambda e: e.get("components") or []
            ),
            "episodes_by_status": _invert(
                episodes, "episode_id", lambda e: [e.get("status")]
            ),
            "episodes_by_engineer": _invert(
                episodes, "episode_id",
                lambda e: [p.get("actor_cluster_id") for p in (e.get("participants") or [])],
            ),
            "engineers_by_role": _invert(
                engineers, "actor_cluster_id", lambda e: e.get("roles_held") or []
            ),
            "engineers_by_strongest_dimension": _invert(
                engineers, "actor_cluster_id", lambda e: [e.get("strongest_dimension")]
            ),
        }
        self._write("indexes.json", indexes)

        payload = {
            "manifest_version": VERSION,
            "generated_at": iso(now()),
            "methodology_version": METHODOLOGY_VERSION,
            "title": "PostHog observable repository impact",
            "subtitle": (
                "Explainable impact analytics over a 90-day public GitHub window"
            ),
            "source": {
                "repository_url": self.pipeline.inputs.repository_url,
                "analyzed_head_sha": self.pipeline.inputs.head_sha,
                "is_shallow_clone": self.pipeline.inputs.is_shallow,
            },
            "window": self.pipeline.inputs.manifest.get("window"),
            "phase1_provenance": self.pipeline.inputs.provenance(),
            "counts": {
                "episodes": len(episodes),
                "engineers": len(engineers),
                "rankable_engineers": sum(1 for e in engineers if e.get("rankable")),
                "claims": len(self.claims),
                "dimension_assessments": len(self.pipeline.dimensions),
                "participants": len(self.pipeline.participants),
                "propagation_edges": len(self.pipeline.propagation_edges),
                "review_interventions": len(self.pipeline.interventions),
            },
            "files": self.files,
            "indexes": {
                "file": "indexes.json",
                "available": sorted(indexes),
            },
            "validation_status": self.validation.get("status"),
            "publishable": bool(self.validation.get("publishable")),
            "publishable_blockers": self.validation.get("publishable_blockers"),
            "limitations_headline": self.config.get(
                "eligibility.limitations.headline"
            ),
            "ui_contract": {
                "render_only_claims": True,
                "claim_lookup": "claims.json -> claims[] keyed by claim_id",
                "never_render": [
                    "any string not resolvable as a claim_id",
                    "a composite score",
                    "a percentage of shared credit",
                ],
                "must_display": [
                    "window start and end",
                    "analyzed_head_sha",
                    "the limitations headline",
                    "unknown-vs-zero distinction on every dimension",
                    "release_corroboration alongside episode status",
                ],
            },
        }
        write_json(self.out / "dashboard_manifest.json", payload)
        return payload

    # -- safety gate ------------------------------------------------------------------
    def safety_scan(self) -> dict[str, Any]:
        hits: list[dict[str, Any]] = []
        forbidden_fields = _forbidden_fields(self.config)
        for path in sorted(self.out.rglob("*.json")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern, label in FORBIDDEN_PATTERNS:
                for match in pattern.findall(text)[:3]:
                    hits.append(
                        {"file": path.name, "kind": label,
                         "sample": str(match)[:40]}
                    )
            if path.name in INFERENCE_SCANNED_FILES:
                for field in forbidden_fields:
                    # As a JSON *key*, i.e. actually carrying such a value.
                    if re.search(rf'"{re.escape(field)}"\s*:', text):
                        hits.append(
                            {"file": path.name, "kind": "forbidden inference field",
                             "sample": field}
                        )
        return {
            "status": "pass" if not hits else "fail",
            "files_scanned": len(list(self.out.rglob("*.json"))),
            "violations": hits[:20],
            "violation_count": len(hits),
            "patterns_checked": [label for _, label in FORBIDDEN_PATTERNS],
            "forbidden_fields_checked": forbidden_fields,
        }

    # -- run -------------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        self.out.mkdir(parents=True, exist_ok=True)
        episodes = self.episodes()
        engineers = self.engineers()
        self.rankings()
        self.comparisons()
        self.evidence()
        self.claims_file()
        self.methodology()
        self.coverage()
        manifest = self.manifest(episodes, engineers)

        safety = self.safety_scan()
        # The violation list is a developer artifact — file names, regex labels
        # and samples. It goes to reports/, not into the published package.
        write_json(
            self.config.paths.reports / "safety_scan.json",
            {**safety, "generated_at": iso(now())},
        )
        manifest["safety_scan"] = {
            "status": safety["status"],
            "files_scanned": safety["files_scanned"],
            "violation_count": safety["violation_count"],
            "report": "reports/phase2/safety_scan.json (not published)",
        }
        if safety["status"] != "pass":
            manifest["publishable"] = False
            manifest.setdefault("publishable_blockers", []).append(
                {"item": "safety_scan", "status": "fail",
                 "detail": f"{safety['violation_count']} violations"}
            )

        # Standing approval: the operator reviewed the methodology, the audit
        # queues and a complete export once, and authorised unattended refreshes
        # of the same pipeline. Human-review blockers are therefore satisfied in
        # advance. A safety-scan failure is NOT covered — that is a data-leak
        # check, not a judgement call, and it still blocks.
        if self.config.get("eligibility.publication.operator_standing_approval", False):
            remaining = [
                b for b in (manifest.get("publishable_blockers") or [])
                if b.get("item") == "safety_scan"
            ]
            manifest["publishable"] = not remaining
            manifest["publishable_blockers"] = remaining
            manifest["approval_mode"] = "operator_standing_approval"
            manifest["approval"] = {
                "approved_by": self.config.get("eligibility.publication.approved_by"),
                "approved_at": self.config.get("eligibility.publication.approved_at"),
                "scope": self.config.get("eligibility.publication.approval_scope"),
                "note": (
                    "Standing approval given once for this pipeline. This run "
                    "was not individually reviewed."
                ),
            }
        else:
            manifest["approval_mode"] = "per_run_human_review"
        write_json(self.out / "dashboard_manifest.json", manifest)

        log.info(
            "exported %d files to %s (publishable=%s)",
            len(self.files) + 1, self.out.name, manifest["publishable"],
        )
        return manifest


def _slim_propagation(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: record.get(k)
        for k in (
            "reach_file_count", "reach_pr_count", "distinct_component_penetration",
            "components_reached", "distinct_downstream_authors", "max_path_depth",
            "mass_after_cap", "cap_applied", "source_age_days", "raw_decay_factor",
            "persistence_detected", "effective_decay_factor", "reason",
        )
    }


def _invert(
    rows: Iterable[Mapping[str, Any]], id_field: str,
    keys: Any,
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for key in keys(row):
            if key:
                out[str(key)].append(str(row[id_field]))
    return {k: sorted(set(v)) for k, v in sorted(out.items())}
