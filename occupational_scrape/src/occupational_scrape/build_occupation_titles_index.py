"""Build the searchable title table: ``data/processed/title_index.parquet``.

A long table, one row per ``(normalized_title, code)`` pair::

    normalized_title  surface_title                  code         source_column
    machine learning engineer  Machine Learning Engineer  15-1221.00  job_title
    machine learning engineer  Machine Learning Engineer  15-1299.08  job_title
    machine learning engineer  Machine Learning Engineer  15-2051.00  job_title

Title -> code is genuinely one-to-many and the table keeps it that way. Collapsing
to one code per title -- or taking the first row of a group -- would fabricate
precision the source data does not have and would corrupt every lift computed
downstream. ``Design Verification Engineer`` is a hardware role at ``17-2061.00``
and a different hardware role at ``17-2071.00``; the resolver's job is to return
both and let the caller disambiguate, not to pick.

Four surface columns are unioned:

``occupation_title``   the official ``Title`` from Occupation Data
``job_title``          ``Job Title`` from Job Titles
``short_title``        ``Short Title`` from Job Titles (O*NET's acronym column)
``reported_title``     ``Reported Job Title`` from Sample of Reported Titles

Normalization is :mod:`occupational_scrape.normalize_job_title`, applied here and
at query time from the same function. Parenthetical acronyms are indexed as their
own keys, so ``Field Programmable Gate Array (FPGA)`` is reachable by ``fpga``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import PROCESSED_DIR
from .fetch_source_files import read_onet_table
from .normalize_job_title import normalization_variants, normalize_title
from .validate_source_release import validate_onet_release

__all__ = [
    "TITLE_INDEX_PATH",
    "SOURCE_COLUMNS",
    "TitleIndex",
    "build_title_index",
    "load_title_index",
    "main",
]

TITLE_INDEX_PATH = PROCESSED_DIR / "title_index.parquet"

# Preference order when several surface titles normalize to the same key for the
# same code. Only affects which surface_title is displayed; the (key, code) pair
# is identical either way. Fixed order keeps the build reproducible.
SOURCE_COLUMNS = ("occupation_title", "short_title", "job_title", "reported_title")
_SOURCE_RANK = {name: rank for rank, name in enumerate(SOURCE_COLUMNS)}

_PLACEHOLDER_SURFACES = {"n/a", "na", "none", ""}


class TitleIndex:
    """Query-side view of ``title_index.parquet``."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        grouped: dict[str, set[str]] = {}
        for key, code in zip(frame["normalized_title"], frame["code"]):
            grouped.setdefault(str(key), set()).add(str(code))
        self._by_key = {key: tuple(sorted(codes)) for key, codes in grouped.items()}

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def normalized_titles(self) -> list[str]:
        """Distinct keys in a fixed order -- the fitting corpus for fuzzy match."""
        return sorted(self._by_key)

    def lookup_exact(self, normalized: str) -> tuple[str, ...]:
        """Codes for an already-normalized key. Sorted, so the result is stable."""
        return self._by_key.get(normalized, ())

    def lookup_raw(self, raw: str) -> tuple[str, ...]:
        """Normalize ``raw`` with the build-time function, then look it up."""
        return self.lookup_exact(normalize_title(raw))


def _collect_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def add(surface: object, code: str, source_column: str) -> None:
        if surface is None or (isinstance(surface, float) and pd.isna(surface)):
            return
        surface = str(surface).strip()
        if surface.casefold() in _PLACEHOLDER_SURFACES:
            return
        for key in normalization_variants(surface):
            rows.append(
                {
                    "normalized_title": key,
                    "surface_title": surface,
                    "code": str(code),
                    "source_column": source_column,
                }
            )

    occupation_data = read_onet_table("occupation_data")
    for code, title in zip(occupation_data["O*NET-SOC Code"], occupation_data["Title"]):
        add(title, code, "occupation_title")

    job_titles = read_onet_table("job_titles")
    for code, job_title, short_title in zip(
        job_titles["O*NET-SOC Code"], job_titles["Job Title"], job_titles["Short Title"]
    ):
        add(job_title, code, "job_title")
        add(short_title, code, "short_title")

    reported = read_onet_table("sample_of_reported_titles")
    for code, title in zip(reported["O*NET-SOC Code"], reported["Reported Job Title"]):
        add(title, code, "reported_title")

    return rows


def build_title_index(*, write: bool = True) -> pd.DataFrame:
    validate_onet_release()

    frame = pd.DataFrame(
        _collect_rows(),
        columns=["normalized_title", "surface_title", "code", "source_column"],
    )
    if frame.empty:
        raise RuntimeError("collected zero title rows from the O*NET release")

    # Deterministic collapse to one row per (normalized_title, code): prefer the
    # most authoritative source column, then the lexicographically first surface.
    frame["_rank"] = frame["source_column"].map(_SOURCE_RANK)
    frame = (
        frame.sort_values(
            ["normalized_title", "code", "_rank", "surface_title"], kind="mergesort"
        )
        .drop_duplicates(subset=["normalized_title", "code"], keep="first")
        .drop(columns=["_rank"])
        .reset_index(drop=True)
    )

    duplicated = frame.duplicated(subset=["normalized_title", "code"]).any()
    if duplicated:
        raise RuntimeError("title index has duplicate (normalized_title, code) pairs")

    frame = frame.astype(
        {
            "normalized_title": "string",
            "surface_title": "string",
            "code": "string",
            "source_column": "string",
        }
    )

    if write:
        TITLE_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(TITLE_INDEX_PATH, index=False)
    return frame


def load_title_index(path: Path | None = None) -> TitleIndex:
    target = path or TITLE_INDEX_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"{target} does not exist. Run build_occupation_titles_index.py first."
        )
    return TitleIndex(pd.read_parquet(target))


def main() -> None:
    frame = build_title_index()
    distinct_keys = frame["normalized_title"].nunique()
    multi = (
        frame.groupby("normalized_title")["code"].nunique().gt(1).sum()
    )
    print(f"wrote {len(frame)} rows to {TITLE_INDEX_PATH}")
    print(f"  distinct normalized titles: {distinct_keys}")
    print(f"  titles mapping to >1 code:  {multi}")
    print(frame["source_column"].value_counts().to_string())


if __name__ == "__main__":
    main()
