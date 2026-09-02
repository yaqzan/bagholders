from database.models.core import AlgorithmVersion


def test_cadence_is_honest_era_only():
    # 2026-06-12 pre-v69 purge: inflated-era versions left the cadence and
    # their score rows were deleted from the scores table. The cadence floor
    # keeps every honest version (v69+) comparable — including future ships —
    # without requiring a DEFAULT_SELECTOR_VERSIONS edit per ship.
    assert AlgorithmVersion.CADENCE_MIN_VERSION_ID == 69
    assert set(AlgorithmVersion.DEFAULT_SELECTOR_VERSIONS) == {69, 70, 71, 72}


def test_selector_dict_never_reaches_below_the_honest_floor():
    # Guard against re-adding an inflated-era version to the seeded cadence.
    assert all(
        version_id >= AlgorithmVersion.CADENCE_MIN_VERSION_ID
        for version_id in AlgorithmVersion.DEFAULT_SELECTOR_VERSIONS
    )
