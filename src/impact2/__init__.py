"""Phase 2 — explainable impact analytics over the Phase 1 evidence contract.

Reads only ``artifacts/`` (contracts/PHASE_2_CONTRACT.md), produces impact episodes,
role-aware attribution, six evidence-banded dimensions, deterministic
propagation/durability analytics, a transparent outranking model, and the
static package Phase 3 renders.
"""

from .versions import METHODOLOGY_VERSION, all_versions

__all__ = ["METHODOLOGY_VERSION", "all_versions"]
