"""Normalization must be identical at build time and query time, or the index is unreachable."""

from __future__ import annotations

import pytest

from occupational_scrape.normalize_job_title import (
    MIN_STEM_LENGTH,
    normalization_variants,
    normalize_title,
    split_parentheticals,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Computer Hardware Engineers", "computer hardware engineer"),
        ("COMPUTER HARDWARE ENGINEERS", "computer hardware engineer"),
        ("  Computer   Hardware  Engineers  ", "computer hardware engineer"),
        ("Electro-Mechanical Technician", "electro-mechanical technician"),
        ("Engineers, All Other", "engineer all other"),
        ("Water/Wastewater Engineers", "water wastewater engineer"),
        # Apostrophe is deleted rather than spaced, then the trailing s is stripped.
        ("Bachelor's Degree Analyst", "bachelor degree analyst"),
        ("R&D Engineer", "r d engineer"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_case_insensitivity_is_total() -> None:
    assert normalize_title("Design Verification Engineer") == normalize_title(
        "design verification engineer"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("engineers", "engineer"),
        ("systems", "system"),
        ("gas", "gas"),  # stem "ga" is under the floor
        ("ics", "ics"),
        ("bus", "bus"),
        ("chips", "chip"),
        # The `ss` guard: without it these degrade one letter per pass.
        ("access", "access"),
        ("process", "process"),
        ("wireless", "wireless"),
    ],
)
def test_trailing_s_stripped_only_above_floor(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected
    if expected != raw:
        assert len(expected) >= MIN_STEM_LENGTH


@pytest.mark.parametrize(
    "raw",
    [
        "Access Control Technician",
        "Process Engineer",
        "Wireless Systems Engineer",
        "Computer Hardware Engineers",
        "Water/Wastewater Engineers",
        "Bachelor's Degree Analyst",
        "R&D Engineer",
        "Electro-Mechanical Technician",
    ],
)
def test_normalization_is_idempotent_across_repeated_application(raw: str) -> None:
    # The fuzzy stage normalizes strings that are already keys. If one pass and two
    # passes disagreed, that stage would search for keys the index cannot hold --
    # which is exactly what `access` -> `acces` -> `acce` used to do.
    once = normalize_title(raw)
    assert normalize_title(once) == once
    assert normalize_title(normalize_title(once)) == once


def test_internal_hyphens_survive_but_edge_hyphens_do_not() -> None:
    assert normalize_title("Electro-Mechanical") == "electro-mechanical"
    assert normalize_title("-Engineer-") == "engineer"


def test_slash_becomes_a_word_break_not_a_join() -> None:
    # "hardwaresoftware" would be an unreachable key; two tokens is the point.
    assert normalize_title("Hardware/Software Engineer") == "hardware software engineer"


def test_split_parentheticals() -> None:
    outer, inner = split_parentheticals("Computer Numerically Controlled (CNC) Operators")
    assert inner == ["CNC"]
    assert "(" not in outer


def test_parenthetical_is_indexed_separately() -> None:
    # O*NET stores acronyms as "Full Name (ACRONYM)". Both must be reachable.
    variants = normalization_variants("Field Programmable Gate Array (FPGA)")
    assert variants[0] == "field programmable gate array"
    assert "fpga" in variants


def test_variants_are_deduplicated_and_ordered() -> None:
    variants = normalization_variants("Engineer (Engineer)")
    assert variants == ("engineer",)


def test_variants_of_empty_input() -> None:
    assert normalization_variants("") == ()
    assert normalization_variants(None) == ()


def test_normalization_is_idempotent() -> None:
    # Query-time input is often already a normalized key from the cache; running
    # normalization on it again must not change it.
    for raw in ["Computer Hardware Engineers", "Water/Wastewater Engineers", "R&D Engineer"]:
        once = normalize_title(raw)
        assert normalize_title(once) == once
