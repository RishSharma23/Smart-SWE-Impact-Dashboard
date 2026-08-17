"""Tier C: semantic *candidate* edges.

Two warnings the phase spec makes explicitly, both honoured here:

1. **A semantic edge cannot merge episodes by itself.**  Every edge emitted by
   this module is marked ``requires_corroboration = True`` and the clustering
   step refuses to act on one that has none.
2. **Keyword scoring is not semantic analysis.**  This module computes TF-IDF
   cosine similarity and says so, on every edge, in ``method`` and
   ``evidence_source``.  It is a *candidate generator* that narrows millions of
   PR pairs down to a few thousand worth looking at.  When the optional LLM
   layer is configured it re-judges those candidates and stamps
   ``method = "llm:<model>@<prompt_version>"``; when it is not, the candidates
   stay labelled as lexical and nothing pretends otherwise.

TF-IDF is implemented here rather than pulled from scikit-learn because the
whole vocabulary fits in memory, the maths is ten lines, and a hand-rolled
version is deterministic across platforms and library versions — which matters
when a published claim has to be reproducible.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

from ..config import Phase2Config, days_between, parse_ts
from .artifact_graph import make_edge

log = logging.getLogger("impact2.graph.semantic")

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
# Code blocks, URLs and HTML comments carry no topical signal and would make
# every PR that includes a stack trace look like every other one.
NOISE_RE = (
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"https?://\S+"),
    re.compile(r"`[^`]*`"),
)


def tokenize(text: str, *, stopwords: set[str], min_length: int) -> list[str]:
    cleaned = text or ""
    for pattern in NOISE_RE:
        cleaned = pattern.sub(" ", cleaned)
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(cleaned.lower()):
        # Split identifiers so `funnelBreakdown` and `funnel_breakdown` agree.
        parts = re.split(r"_+", raw)
        expanded: list[str] = []
        for part in parts:
            expanded.extend(
                p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", part)
            ) or expanded.append(part)
        for token in (expanded or parts):
            if len(token) >= min_length and token not in stopwords:
                tokens.append(token)
    return tokens


class TfidfIndex:
    """Deterministic TF-IDF over PR text, with document-frequency pruning."""

    def __init__(
        self,
        documents: Mapping[int, str],
        *,
        stopwords: set[str],
        min_length: int,
        max_df_ratio: float,
    ) -> None:
        self.doc_count = max(1, len(documents))
        raw_tokens = {
            number: tokenize(text, stopwords=stopwords, min_length=min_length)
            for number, text in documents.items()
        }
        df: dict[str, int] = defaultdict(int)
        for tokens in raw_tokens.values():
            for token in set(tokens):
                df[token] += 1
        # A token in a third of all PRs ("frontend", "posthog") separates
        # nothing; pruning it is what stops everything looking similar.
        self.max_df = max(2, int(self.doc_count * max_df_ratio))
        self.df = {t: c for t, c in df.items() if 1 < c <= self.max_df}
        self.pruned_tokens = len(df) - len(self.df)

        self.vectors: dict[int, dict[str, float]] = {}
        for number, tokens in raw_tokens.items():
            counts: dict[str, int] = defaultdict(int)
            for token in tokens:
                if token in self.df:
                    counts[token] += 1
            if not counts:
                self.vectors[number] = {}
                continue
            vector: dict[str, float] = {}
            for token, count in counts.items():
                tf = 1.0 + math.log(count)
                idf = math.log((1.0 + self.doc_count) / (1.0 + self.df[token])) + 1.0
                vector[token] = tf * idf
            norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
            self.vectors[number] = {t: v / norm for t, v in vector.items()}

        # Inverted index so candidate generation is not O(n^2) over 20k PRs.
        self.postings: dict[str, list[int]] = defaultdict(list)
        for number, vector in sorted(self.vectors.items()):
            for token in sorted(vector):
                self.postings[token].append(number)

    def similarity(self, a: int, b: int) -> float:
        va, vb = self.vectors.get(a) or {}, self.vectors.get(b) or {}
        if not va or not vb:
            return 0.0
        if len(va) > len(vb):
            va, vb = vb, va
        return round(sum(weight * vb.get(token, 0.0) for token, weight in va.items()), 6)

    def candidates(self, number: int, *, max_postings: int = 400) -> set[int]:
        """PRs sharing at least one surviving token, cheapest-first.

        Rare tokens are scanned first and very common postings lists are
        skipped: they contribute little discrimination and a lot of work.
        """
        vector = self.vectors.get(number) or {}
        out: set[int] = set()
        for token in sorted(vector, key=lambda t: (self.df.get(t, 0), t)):
            posting = self.postings.get(token) or []
            if len(posting) > max_postings:
                continue
            out.update(posting)
        out.discard(number)
        return out


def build_semantic_edges(
    config: Phase2Config,
    prs: Mapping[int, Mapping[str, Any]],
    *,
    components_by_pr: Mapping[int, set[str]],
    corroborated_pairs: set[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Emit Tier C candidate edges.

    ``corroborated_pairs`` is the set of PR pairs that already have a Tier A or
    Tier B edge.  A semantic edge between a corroborated pair is *evidence*; a
    semantic edge between an uncorroborated pair is a *question*, and is
    emitted with ``usable_for_clustering = False`` so it can be reviewed
    without silently reshaping the episode map.
    """
    settings = config.get("episodes.semantic")
    stopwords = set(settings.get("stopwords") or [])
    min_similarity = float(settings["min_similarity"])
    max_days = float(settings["max_days_apart"])
    max_candidates = int(settings["max_candidates_per_pr"])
    require_component_overlap = bool(settings.get("require_component_overlap", True))

    documents = {
        number: " ".join(
            [
                str(pr.get("title_raw") or ""),
                str(pr.get("title_subject") or ""),
                str(pr.get("body_text") or "")[:4000],
            ]
        )
        for number, pr in prs.items()
    }
    index = TfidfIndex(
        documents,
        stopwords=stopwords,
        min_length=int(settings["min_token_length"]),
        max_df_ratio=float(settings["max_document_frequency_ratio"]),
    )
    log.info(
        "tf-idf: %d documents, %d vocabulary terms kept, %d pruned as too common "
        "(df > %d)",
        index.doc_count, len(index.df), index.pruned_tokens, index.max_df,
    )

    merged = {n: parse_ts(p.get("merged_at")) or parse_ts(p.get("created_at"))
              for n, p in prs.items()}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    for number in sorted(prs):
        scored: list[tuple[float, int]] = []
        for other in index.candidates(number):
            pair = (min(number, other), max(number, other))
            if pair in seen:
                continue
            span = days_between(merged.get(number), merged.get(other))
            if span is None or abs(span) > max_days:
                continue
            if require_component_overlap:
                shared = (components_by_pr.get(number) or set()) & (
                    components_by_pr.get(other) or set()
                )
                if not shared:
                    continue
            score = index.similarity(number, other)
            if score >= min_similarity:
                scored.append((score, other))

        scored.sort(key=lambda item: (-item[0], item[1]))
        for score, other in scored[:max_candidates]:
            pair = (min(number, other), max(number, other))
            if pair in seen:
                continue
            seen.add(pair)
            corroborated = pair in corroborated_pairs
            shared = sorted(
                (components_by_pr.get(number) or set())
                & (components_by_pr.get(other) or set())
            )[:4]
            edges.append(
                make_edge(
                    source_kind="pull_request", source_key=pair[0],
                    target_kind="pull_request", target_key=pair[1],
                    edge_type="semantic_similarity", tier="C",
                    evidence=(
                        f"lexical cosine similarity {score:.3f} over title+body; "
                        f"{abs(days_between(merged.get(pair[0]), merged.get(pair[1])) or 0):.1f} "
                        f"days apart; shared components {shared or 'none'}"
                    ),
                    evidence_source="phase2:tfidf_cosine",
                    config=config,
                    guards=[] if corroborated else ["requires_corroboration"],
                    usable_for_clustering=corroborated,
                    source_time=merged.get(pair[0]),
                    target_time=merged.get(pair[1]),
                    extra={
                        "method": "tfidf_cosine",
                        "similarity": score,
                        "requires_corroboration": True,
                        "corroborated_by_other_edge": corroborated,
                        "shared_components": shared,
                        "llm_status": "not_requested",
                    },
                )
            )

    log.info(
        "semantic candidates: %d edges, %d corroborated by a tier A/B edge",
        len(edges), sum(1 for e in edges if e.get("corroborated_by_other_edge")),
    )
    return edges


def summarise(edges: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [e for e in edges if e.get("tier") == "C"]
    scores = [float(e.get("similarity") or 0.0) for e in items]
    return {
        "semantic_candidates": len(items),
        "corroborated": sum(1 for e in items if e.get("corroborated_by_other_edge")),
        "usable_for_clustering": sum(1 for e in items if e.get("usable_for_clustering")),
        "mean_similarity": round(sum(scores) / len(scores), 4) if scores else None,
        "method": "tfidf_cosine",
        "note": (
            "Lexical similarity is a candidate generator, not semantic "
            "understanding. Edges are labelled with the method that produced "
            "them and never merge episodes without corroboration."
        ),
    }
