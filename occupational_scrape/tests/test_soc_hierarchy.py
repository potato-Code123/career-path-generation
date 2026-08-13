"""The backoff ladder must be a well-formed tree that agrees with the BLS file."""

from __future__ import annotations

import pandas as pd
import pytest

from occupational_scrape.build_soc_hierarchy import (
    LEVELS,
    decompose_code,
    load_soc_hierarchy,
)
from occupational_scrape.fetch_source_files import read_bls_soc_structure


@pytest.fixture(scope="module")
def frame(hierarchy) -> pd.DataFrame:
    return hierarchy.frame


def test_levels_are_from_the_declared_set(frame: pd.DataFrame) -> None:
    assert set(frame["level"]) <= set(LEVELS)


def test_every_non_major_node_has_a_parent_present_in_the_table(frame: pd.DataFrame) -> None:
    codes = set(frame["code"])
    missing = [
        (row.code, row.parent)
        for row in frame.itertuples()
        if row.level != "major" and (pd.isna(row.parent) or row.parent not in codes)
    ]
    assert missing == [], f"{len(missing)} nodes have a dangling parent: {missing[:10]}"


def test_major_groups_are_the_only_roots(frame: pd.DataFrame) -> None:
    roots = frame[frame["parent"].isna()]
    assert set(roots["level"]) == {"major"}
    assert len(roots) == int((frame["level"] == "major").sum())


def test_no_cycles(hierarchy, frame: pd.DataFrame) -> None:
    for code in frame["code"]:
        ladder = hierarchy.ancestors(code)
        assert len(ladder) == len(set(ladder)), f"repeated node in the ladder for {code}"


def test_ancestors_of_computer_hardware_engineers(hierarchy) -> None:
    assert hierarchy.ancestors("17-2061.00") == [
        "17-2061.00",
        "17-2061",
        "17-2060",
        "17-2000",
        "17-0000",
    ]


def test_ancestors_are_most_specific_first(hierarchy) -> None:
    from occupational_scrape.build_soc_hierarchy import LEVEL_ORDER

    ladder = hierarchy.ancestors("15-1221.00")
    orders = [LEVEL_ORDER[hierarchy.level(code)] for code in ladder]
    assert orders == sorted(orders, reverse=True)


def test_ancestors_of_unknown_code_is_empty(hierarchy) -> None:
    assert hierarchy.ancestors("99-9999.99") == []


def test_node_count_matches_the_bls_structure_file(frame: pd.DataFrame) -> None:
    bls_rows = len(read_bls_soc_structure())
    derived = int((frame["level"] != "onet_detail").sum())
    assert derived == bls_rows


def test_onet_detail_nodes_hang_off_their_own_soc_stem(frame: pd.DataFrame) -> None:
    onet = frame[frame["level"] == "onet_detail"]
    assert len(onet) > 0
    mismatched = [
        row.code for row in onet.itertuples() if row.parent != row.code.split(".")[0]
    ]
    assert mismatched == []


def test_bls_overrules_string_decomposition_for_the_15_12_family(hierarchy) -> None:
    # The motivating irregularity: 15-1200 is a minor group whose fourth digit is
    # non-zero, so decomposition proposes the non-existent 15-1000.
    assert decompose_code("15-1220")["minor"] == "15-1000"
    assert "15-1000" not in hierarchy
    assert hierarchy.ancestors("15-1220")[1] == "15-1200"


def test_broad_and_detailed_may_share_a_title_without_collapsing(hierarchy) -> None:
    # 11-1010 and 11-1011 are both "Chief Executives"; they must stay distinct
    # nodes with a real parent edge between them.
    assert hierarchy.title("11-1010") == hierarchy.title("11-1011")
    assert hierarchy.ancestors("11-1011")[1] == "11-1010"


def test_residual_all_other_codes_are_present_as_backoff_targets(hierarchy) -> None:
    # Excluded from the leaf set, but they must still exist in the ladder.
    assert "17-2199.00" in hierarchy
    assert hierarchy.ancestors("17-2199.00")[-1] == "17-0000"


def test_descendants_of_a_minor_group_include_its_onet_leaves(hierarchy) -> None:
    descendants = hierarchy.descendants("17-2000")
    assert "17-2061.00" in descendants
    assert "17-2061" in descendants
    assert "17-2000" not in descendants
