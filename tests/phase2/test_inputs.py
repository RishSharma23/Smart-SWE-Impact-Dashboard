

def test_operational_columns_match_phase1():
    """Phase 2 re-computes Phase 1's hashes, so the exclude lists must agree.

    Drift here does not raise — it silently reports every provenance table as
    corrupted, which is how it was found the first time.
    """
    from impact.hashing import OPERATIONAL_COLUMNS as phase1
    from impact2.ids import OPERATIONAL_COLUMNS as phase2

    assert tuple(phase2) == tuple(phase1)
