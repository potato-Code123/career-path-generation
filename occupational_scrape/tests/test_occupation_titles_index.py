"""The title index must stay one-to-many and must be reachable by the query-time key."""

from __future__ import annotations

import pandas as pd
import pytest

from occupational_scrape.build_occupation_titles_index import (
    SOURCE_COLUMNS,
    load_title_index,
)
from occupational_scrape.normalize_job_title import normalize_title


@pytest.fixture(scope="module")
def frame(title_index) -> pd.DataFrame:
    return title_index.frame


def test_schema(frame: pd.DataFrame) -> None:
    assert list(frame.columns) == [
        "normalized_title",
        "surface_title",
        "code",
        "source_column",
    ]


def test_one_row_per_normalized_title_and_code(frame: pd.DataFrame) -> None:
    duplicates = frame[frame.duplicated(subset=["normalized_title", "code"], keep=False)]
    assert duplicates.empty, f"{len(duplicates)} duplicate (normalized_title, code) rows"


def test_source_columns_are_from_the_declared_set(frame: pd.DataFrame) -> None:
    assert set(frame["source_column"]) <= set(SOURCE_COLUMNS)


def test_every_official_occupation_title_is_indexed(frame: pd.DataFrame) -> None:
    from occupational_scrape.fetch_source_files import read_onet_table

    occupation_data = read_onet_table("occupation_data")
    indexed = set(frame.loc[frame["source_column"] == "occupation_title", "code"])
    assert indexed == set(occupation_data["O*NET-SOC Code"])


def test_keys_are_already_normalized(frame: pd.DataFrame) -> None:
    # If a stored key does not survive its own normalization, query-time lookup
    # can never reach it.
    sample = frame["normalized_title"].drop_duplicates().head(2000)
    offenders = [key for key in sample if normalize_title(key) != key]
    assert offenders == [], offenders[:10]


def test_title_to_code_stays_one_to_many(title_index) -> None:
    # These are the documented cases. Collapsing any of them to a single code
    # would fabricate precision the source data does not contain.
    assert set(title_index.lookup_raw("Machine Learning Engineer")) == {
        "15-1221.00",
        "15-1299.08",
        "15-2051.00",
    }
    assert set(title_index.lookup_raw("Design Verification Engineer")) == {
        "17-2061.00",
        "17-2071.00",
    }


def test_lookup_is_case_insensitive(title_index) -> None:
    assert title_index.lookup_raw("DESIGN VERIFICATION ENGINEER") == title_index.lookup_raw(
        "design verification engineer"
    )


def test_lookup_returns_sorted_codes(title_index) -> None:
    codes = title_index.lookup_raw("Machine Learning Engineer")
    assert list(codes) == sorted(codes)


def test_unknown_title_returns_empty(title_index) -> None:
    assert title_index.lookup_raw("asdfqwer") == ()


def test_acronyms_are_reachable_via_the_short_title_column(frame: pd.DataFrame) -> None:
    short = frame[frame["source_column"] == "short_title"]
    assert len(short) > 0


def test_normalized_titles_are_sorted_and_distinct(title_index) -> None:
    keys = title_index.normalized_titles
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_rebuild_is_byte_stable(tmp_path) -> None:
    from occupational_scrape.build_occupation_titles_index import build_title_index

    first = build_title_index(write=False)
    second = build_title_index(write=False)
    pd.testing.assert_frame_equal(first, second)
