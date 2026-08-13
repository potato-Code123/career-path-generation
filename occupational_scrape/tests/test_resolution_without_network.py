"""Stages 1-3 must not depend on Claude.

The LLM client is replaced with one that raises on every call. Every exit case
whose expected stage is ``exact``, ``expanded`` or ``fuzzy`` must still produce
its expected codes -- if any of them silently depends on the model, the pipeline
cannot be rebuilt offline and cannot be reproduced by anyone without an API key.
"""

from __future__ import annotations

import pytest
from conftest import CASES, Case, RaisingLlmClient

from occupational_scrape.resolve_career_title import resolve_career_title
from occupational_scrape.select_candidate_via_llm import (
    Candidate,
    LlmUnavailable,
    select_candidate,
)

NETWORK_FREE_CASES = tuple(case for case in CASES if case.network_free)


def test_the_table_actually_covers_the_network_free_stages() -> None:
    stages = {stage for case in NETWORK_FREE_CASES for stage in case.allowed_stages}
    assert {"exact", "expanded", "fuzzy"} <= stages


@pytest.mark.parametrize("case", NETWORK_FREE_CASES, ids=lambda case: case.input_raw)
def test_case_resolves_without_the_llm(
    case: Case, title_index, matcher, temp_cache
) -> None:
    resolution = resolve_career_title(
        case.input_raw,
        index=title_index,
        matcher=matcher,
        cache=temp_cache,
        client=RaisingLlmClient(),
    )

    assert resolution.stage in case.allowed_stages
    if case.expect_exact_codes is not None:
        assert set(resolution.codes) == set(case.expect_exact_codes)
    if case.expect_includes:
        assert case.expect_includes <= set(resolution.codes)
    if case.expect_non_empty:
        assert resolution.codes != ()


def test_default_client_is_never_constructed_when_stages_1_to_3_hit(
    title_index, matcher, temp_cache, monkeypatch
) -> None:
    import occupational_scrape.resolve_career_title as resolver

    def explode() -> None:
        raise AssertionError("stage 4 was reached for an input that stages 1-3 resolve")

    monkeypatch.setattr(resolver, "get_default_client", explode)

    for case in NETWORK_FREE_CASES:
        resolve_career_title(
            case.input_raw, index=title_index, matcher=matcher, cache=temp_cache
        )


def test_llm_failure_degrades_to_unmapped_rather_than_raising(
    title_index, matcher, temp_cache
) -> None:
    resolution = resolve_career_title(
        "prompt engineer",
        index=title_index,
        matcher=matcher,
        cache=temp_cache,
        client=RaisingLlmClient(),
    )
    assert resolution.stage == "unmapped"
    assert resolution.codes == ()


def test_an_unreachable_llm_is_not_cached(title_index, matcher, temp_cache) -> None:
    """A transient outage must not permanently pin an input to `unmapped`."""
    resolve_career_title(
        "prompt engineer",
        index=title_index,
        matcher=matcher,
        cache=temp_cache,
        client=RaisingLlmClient(),
    )
    from occupational_scrape.normalize_job_title import normalize_title

    assert temp_cache.lookup(normalize_title("prompt engineer")) is None


def test_use_llm_false_short_circuits_stage_four(title_index, matcher, temp_cache) -> None:
    resolution = resolve_career_title(
        "prompt engineer",
        index=title_index,
        matcher=matcher,
        cache=temp_cache,
        client=RaisingLlmClient(),
        use_llm=False,
    )
    assert resolution.stage == "unmapped"


class TestCandidateContainment:
    """The model can never introduce a code stage 3 did not propose."""

    CANDIDATES = (
        Candidate("17-2061.00", "Computer Hardware Engineers", "Research, design, develop..."),
        Candidate("17-2071.00", "Electrical Engineers", "Research, design, develop..."),
    )

    def _client(self, answer: str):
        class Fixed:
            def complete(self, prompt: str, schema: dict) -> str:  # noqa: ARG002
                return answer

        return Fixed()

    def test_valid_choice_is_returned(self) -> None:
        chosen = select_candidate("x", self.CANDIDATES, self._client('{"code": "17-2061.00"}'))
        assert chosen == "17-2061.00"

    def test_unmapped_is_returned_as_none(self) -> None:
        assert select_candidate("x", self.CANDIDATES, self._client('{"code": "UNMAPPED"}')) is None

    def test_code_outside_the_candidate_set_is_rejected(self) -> None:
        # Validation, not prompt wording, is what enforces this.
        assert select_candidate("x", self.CANDIDATES, self._client('{"code": "29-1292.00"}')) is None

    def test_garbage_answer_is_rejected(self) -> None:
        assert select_candidate("x", self.CANDIDATES, self._client("I think it's a hardware role")) is None

    def test_empty_answer_is_rejected(self) -> None:
        assert select_candidate("x", self.CANDIDATES, self._client("")) is None

    def test_empty_candidate_set_never_calls_the_model(self) -> None:
        class Exploding:
            def complete(self, prompt: str, schema: dict) -> str:  # noqa: ARG002
                raise AssertionError("called with no candidates")

        assert select_candidate("x", (), Exploding()) is None

    def test_schema_enum_is_restricted_to_the_candidates(self) -> None:
        captured: dict = {}

        class Capturing:
            def complete(self, prompt: str, schema: dict) -> str:
                captured.update(schema)
                return '{"code": "UNMAPPED"}'

        select_candidate("x", self.CANDIDATES, Capturing())
        enum = captured["properties"]["code"]["enum"]
        assert set(enum) == {"17-2061.00", "17-2071.00", "UNMAPPED"}

    def test_unavailable_client_propagates_rather_than_returning_a_code(self) -> None:
        class Unavailable:
            def complete(self, prompt: str, schema: dict) -> str:  # noqa: ARG002
                raise LlmUnavailable("down")

        with pytest.raises(LlmUnavailable):
            select_candidate("x", self.CANDIDATES, Unavailable())
