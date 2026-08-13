"""Table-driven exit cases for the resolution cascade.

Each case asserts **both** the resolved code set and the stage reached. Asserting
only the codes would let a regression that pushes an exact match down into the
LLM stage pass silently -- the answer would still be right, but it would now cost
a model call per novel title and would stop being reproducible from the index
alone. The stage assertion is the one that catches that.
"""

from __future__ import annotations

import pytest
from conftest import CASES, Case, StubLlmClient

from occupational_scrape.apply_career_backoff import load_ece_leaf_set
from occupational_scrape.resolve_career_title import STAGES, resolve_career_title


def _resolve(case: Case, title_index, matcher, temp_cache):
    # A deterministic stub stands in for Claude so the suite never needs network
    # access; cases whose expected stage is exact/expanded/fuzzy never reach it.
    return resolve_career_title(
        case.input_raw,
        index=title_index,
        matcher=matcher,
        cache=temp_cache,
        client=StubLlmClient(),
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.input_raw)
def test_exit_case(case: Case, title_index, matcher, temp_cache) -> None:
    resolution = _resolve(case, title_index, matcher, temp_cache)

    assert resolution.stage in STAGES
    assert resolution.stage in case.allowed_stages, (
        f"{case.input_raw!r} resolved at stage {resolution.stage!r}; "
        f"expected one of {case.allowed_stages}. {case.note}"
    )

    if case.expect_exact_codes is not None:
        assert set(resolution.codes) == set(case.expect_exact_codes)

    if case.expect_includes:
        assert case.expect_includes <= set(resolution.codes), (
            f"{case.input_raw!r} resolved to {resolution.codes}, "
            f"missing {sorted(case.expect_includes - set(resolution.codes))}"
        )

    if case.expect_empty:
        assert resolution.codes == ()

    if case.expect_non_empty:
        assert resolution.codes != ()

    if case.forbid_ece_leaf:
        leaves = load_ece_leaf_set()
        leaked = sorted(set(resolution.codes) & leaves)
        assert leaked == [], f"{case.input_raw!r} leaked into the ECE namespace: {leaked}"


def test_prompt_engineer_is_never_an_exact_match(title_index, matcher, temp_cache) -> None:
    resolution = resolve_career_title(
        "prompt engineer",
        index=title_index,
        matcher=matcher,
        cache=temp_cache,
        client=StubLlmClient(),
    )
    assert resolution.stage != "exact"


def test_case_variants_resolve_identically(title_index, matcher, temp_cache) -> None:
    lower = resolve_career_title(
        "design verification engineer", index=title_index, matcher=matcher, cache=temp_cache
    )
    upper = resolve_career_title(
        "Design Verification Engineer", index=title_index, matcher=matcher, cache=temp_cache
    )
    assert lower.codes == upper.codes
    assert lower.stage == upper.stage


def test_ml_engineer_and_mle_agree(title_index, matcher, temp_cache) -> None:
    long_form = resolve_career_title(
        "ML engineer", index=title_index, matcher=matcher, cache=temp_cache
    )
    acronym = resolve_career_title("MLE", index=title_index, matcher=matcher, cache=temp_cache)
    assert set(long_form.codes) == set(acronym.codes)
    assert long_form.stage == acronym.stage == "expanded"


def test_candidates_considered_is_populated_for_non_exact_stages(
    title_index, matcher, temp_cache
) -> None:
    resolution = resolve_career_title(
        "RTL designer", index=title_index, matcher=matcher, cache=temp_cache
    )
    assert resolution.candidates_considered != ()
    assert set(resolution.codes) <= set(resolution.candidates_considered)


def test_backoff_level_is_unset_until_apply_backoff_runs(
    title_index, matcher, temp_cache
) -> None:
    resolution = resolve_career_title(
        "embedded engineer", index=title_index, matcher=matcher, cache=temp_cache
    )
    assert resolution.backoff_level is None


class TestBackoff:
    """Step 7 behaviour, asserted on real resolutions."""

    def test_single_survivor_is_a_leaf(self, hierarchy, title_index, matcher, temp_cache):
        from occupational_scrape.apply_career_backoff import apply_backoff

        resolution = resolve_career_title(
            "embedded engineer", index=title_index, matcher=matcher, cache=temp_cache
        )
        backed_off = apply_backoff(resolution, hierarchy=hierarchy)
        assert backed_off.backoff_level == "leaf"
        assert backed_off.codes == ("15-1252.00",)

    def test_multiple_survivors_are_ambiguous_and_not_picked(
        self, hierarchy, title_index, matcher, temp_cache
    ):
        from occupational_scrape.apply_career_backoff import apply_backoff

        resolution = resolve_career_title(
            "FPGA engineer", index=title_index, matcher=matcher, cache=temp_cache
        )
        backed_off = apply_backoff(resolution, hierarchy=hierarchy)
        assert backed_off.backoff_level == "ambiguous"
        assert set(backed_off.codes) == {"17-2061.00", "17-2072.00"}

    def test_empty_resolution_gets_no_backoff_level(self, hierarchy):
        from occupational_scrape.apply_career_backoff import apply_backoff
        from occupational_scrape.resolve_career_title import Resolution

        empty = Resolution(input_raw="asdfqwer", codes=(), stage="unmapped", candidates_considered=())
        assert apply_backoff(empty, hierarchy=hierarchy).backoff_level is None

    def test_walk_terminates_at_the_level_that_first_contains_a_leaf(self, hierarchy):
        from occupational_scrape.apply_career_backoff import apply_backoff
        from occupational_scrape.resolve_career_title import Resolution

        # 17-2141.00 (Mechanical Engineers) is not an ECE leaf, but its minor
        # group 17-2000 contains several.
        resolution = Resolution(
            input_raw="mechanical engineer",
            codes=("17-2141.00",),
            stage="exact",
            candidates_considered=("17-2141.00",),
        )
        backed_off = apply_backoff(resolution, hierarchy=hierarchy)
        assert backed_off.backoff_level in {"broad", "minor", "major"}
        assert backed_off.codes != ()
        assert set(backed_off.codes) <= load_ece_leaf_set()

    def test_cip_prior_is_the_last_resort(self, hierarchy):
        from occupational_scrape.apply_career_backoff import apply_backoff
        from occupational_scrape.resolve_career_title import Resolution

        # 35-2011.00 (Cooks) shares no ancestor with any ECE leaf, so the ladder
        # is exhausted and the department's CIP prior takes over.
        resolution = Resolution(
            input_raw="line cook",
            codes=("35-2011.00",),
            stage="exact",
            candidates_considered=("35-2011.00",),
        )
        backed_off = apply_backoff(resolution, hierarchy=hierarchy)
        assert backed_off.backoff_level == "cip_prior"
        assert set(backed_off.codes) <= load_ece_leaf_set()
        assert backed_off.codes != ()

    def test_backoff_never_returns_a_residual_all_other_bucket(self, hierarchy):
        from occupational_scrape.apply_career_backoff import apply_backoff
        from occupational_scrape.resolve_career_title import Resolution

        residual = {"17-2199.00", "15-1299.00", "15-2099.00", "17-3019.00", "17-3029.00"}
        for code in ("17-2141.00", "35-2011.00", "17-2011.00"):
            resolution = Resolution(
                input_raw=code, codes=(code,), stage="exact", candidates_considered=(code,)
            )
            backed_off = apply_backoff(resolution, hierarchy=hierarchy)
            assert not (set(backed_off.codes) & residual)


def test_leaf_set_excludes_blank_data_level_codes() -> None:
    """Residual "All Other" buckets are backoff targets, never resolution targets."""
    from occupational_scrape.fetch_source_files import read_all_occupations

    all_occupations = read_all_occupations()
    residual = set(all_occupations.loc[all_occupations["Data-level"].isna(), "Code"])
    assert not (load_ece_leaf_set() & residual)


def test_leaf_set_size_is_within_the_curated_range() -> None:
    assert 10 <= len(load_ece_leaf_set()) <= 15
