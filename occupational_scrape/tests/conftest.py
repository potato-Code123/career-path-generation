"""Shared fixtures and the resolution exit-case table.

The exit-case table lives here rather than in one test module because three
different tests assert against it: the behavioural test, the determinism test,
and the no-network test. Keeping one copy means a regression cannot be hidden by
updating the table in one place and not the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from occupational_scrape.build_occupation_titles_index import load_title_index
from occupational_scrape.build_soc_hierarchy import load_soc_hierarchy
from occupational_scrape.match_title_fuzzy import FuzzyMatcher, load_fuzzy_settings
from occupational_scrape.resolution_cache import ResolutionCache
from occupational_scrape.select_candidate_via_llm import LlmUnavailable

ECE_PREFIXES = ("17-2", "17-3", "15-12", "15-2")


@dataclass(frozen=True)
class Case:
    """One row of the phase-2 exit-case table."""

    input_raw: str
    allowed_stages: tuple[str, ...]
    expect_exact_codes: frozenset[str] | None = None
    expect_includes: frozenset[str] = frozenset()
    expect_empty: bool = False
    expect_non_empty: bool = False
    forbid_ece_leaf: bool = False
    note: str = ""

    @property
    def network_free(self) -> bool:
        """True when the expected stage is reachable without Claude."""
        return set(self.allowed_stages) <= {"exact", "expanded", "fuzzy"}


CASES: tuple[Case, ...] = (
    Case(
        "design verification engineer",
        ("exact",),
        expect_exact_codes=frozenset({"17-2061.00", "17-2071.00"}),
    ),
    Case(
        "Design Verification Engineer",
        ("exact",),
        expect_exact_codes=frozenset({"17-2061.00", "17-2071.00"}),
        note="case-insensitive; must be identical to the lowercase form",
    ),
    Case(
        "ML engineer",
        ("expanded",),
        expect_includes=frozenset({"15-1221.00", "15-1299.08", "15-2051.00"}),
    ),
    Case(
        "MLE",
        ("expanded",),
        expect_includes=frozenset({"15-1221.00", "15-1299.08", "15-2051.00"}),
        note="same set as 'ML engineer'",
    ),
    Case(
        "RTL designer",
        ("expanded", "fuzzy"),
        expect_includes=frozenset({"17-2061.00"}),
        note="O*NET has zero rows for RTL; only reachable via expansion",
    ),
    Case(
        "FPGA engineer",
        ("exact",),
        expect_exact_codes=frozenset({"17-2061.00", "17-2072.00"}),
    ),
    Case("embedded engineer", ("exact",), expect_includes=frozenset({"15-1252.00"})),
    Case("chip designer", ("fuzzy", "llm"), expect_non_empty=True),
    Case(
        "quant",
        ("fuzzy", "llm", "unmapped"),
        forbid_ece_leaf=True,
        note="a finance/math code or unmapped, but never an ECE leaf",
    ),
    Case("prompt engineer", ("llm", "unmapped", "fuzzy"), note="must not be exact"),
    Case(
        "power systems engineer",
        ("exact", "expanded"),
        expect_includes=frozenset({"17-2071.00"}),
    ),
    Case("asdfqwer", ("unmapped",), expect_empty=True),
)


class RaisingLlmClient:
    """Stands in for an unreachable Claude. Stage 4 must never be required."""

    def complete(self, prompt: str, schema: dict) -> str:  # noqa: ARG002
        raise LlmUnavailable("network disabled for this test")


class StubLlmClient:
    """Deterministic selector: always answers UNMAPPED.

    Used where a test needs stage 4 to actually run and be cached without making
    a network call.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, prompt: str, schema: dict) -> str:  # noqa: ARG002
        self.calls.append(prompt)
        return '{"code": "UNMAPPED"}'


@pytest.fixture(scope="session")
def title_index():
    return load_title_index()


@pytest.fixture(scope="session")
def hierarchy():
    return load_soc_hierarchy()


@pytest.fixture(scope="session")
def matcher(title_index):
    # Fitting the vectorizer over ~50k keys is the expensive part of the suite;
    # do it once.
    return FuzzyMatcher(title_index, load_fuzzy_settings())


@pytest.fixture
def temp_cache(tmp_path: Path) -> ResolutionCache:
    """A cache backed by a throwaway file, so tests never touch cache/."""
    return ResolutionCache(tmp_path / "career_title_resolutions.csv")


def is_ece_leaf(code: str, leaf_set: frozenset[str]) -> bool:
    return code in leaf_set
