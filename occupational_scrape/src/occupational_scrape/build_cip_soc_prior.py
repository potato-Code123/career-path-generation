"""Build ``data/processed/cip_soc_prior.parquet`` from the NCES CIP-SOC crosswalk.

Filters the crosswalk to CIP series 14.09 (Computer Engineering) and 14.10
(Electrical, Electronics and Communications Engineering) and keeps the relation
long: CIP-to-SOC is many-to-many in both directions and collapsing either side
would invent a precision the source does not have.

**This crosswalk is a prior, not evidence.** It is expert-constructed: analysts
read a CIP program description and a SOC occupation description and judged them
related. It is not derived from any empirical placement data -- nobody observed a
single graduate taking a single job to build it. It therefore says what a
department's graduates *plausibly could* do, never what they *actually did*.

That distinction has teeth downstream. The statistics layer computes lift over
observed transcripts; this table is only ever consulted at the very bottom of
:func:`occupational_scrape.apply_career_backoff.apply_backoff`, when the SOC
ladder has been exhausted and there is no observed signal left to condition on.
Any explanation surfaced to a student from a ``cip_prior`` backoff must be worded
as "programs like yours are associated with these occupations", never as
"graduates of your program become these things". Conflating the two would present
an analyst's judgement as a measurement.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import PROCESSED_DIR
from .fetch_source_files import read_cip_soc_crosswalk
from .validate_source_release import CROSSWALK_SENTINEL_CODES

__all__ = [
    "CIP_PRIOR_PATH",
    "DEPARTMENT_CIP_SERIES",
    "build_cip_soc_prior",
    "load_cip_soc_prior",
    "main",
]

CIP_PRIOR_PATH = PROCESSED_DIR / "cip_soc_prior.parquet"

DEPARTMENT_CIP_SERIES = ("14.09", "14.10")
"""14.09 Computer Engineering; 14.10 Electrical, Electronics and Communications
Engineering. These two series are the department's own CIP codes and are what the
``cip_prior`` backoff level falls back to."""


def build_cip_soc_prior(*, write: bool = True) -> pd.DataFrame:
    frame = read_cip_soc_crosswalk()

    selected = frame[
        frame["cip_code"].fillna("").str.startswith(DEPARTMENT_CIP_SERIES)
    ].copy()
    if selected.empty:
        raise RuntimeError(
            f"no crosswalk rows matched CIP series {DEPARTMENT_CIP_SERIES}; "
            "the crosswalk layout or CIP edition changed"
        )

    # 99-9999 is the NCES "no SOC match" sentinel, not an occupation. Keeping it
    # would put a code in the prior that the hierarchy cannot resolve.
    selected = selected[~selected["soc_code"].isin(CROSSWALK_SENTINEL_CODES)]

    selected = (
        selected[["cip_code", "cip_title", "soc_code"]]
        .dropna(subset=["cip_code", "soc_code"])
        .drop_duplicates()
        .sort_values(["cip_code", "soc_code"], kind="mergesort")
        .reset_index(drop=True)
        .astype({"cip_code": "string", "cip_title": "string", "soc_code": "string"})
    )

    if write:
        CIP_PRIOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        selected.to_parquet(CIP_PRIOR_PATH, index=False)
    return selected


def load_cip_soc_prior(path: Path | None = None) -> pd.DataFrame:
    target = path or CIP_PRIOR_PATH
    if not target.exists():
        raise FileNotFoundError(f"{target} does not exist. Run build_cip_soc_prior.py first.")
    return pd.read_parquet(target)


def main() -> None:
    frame = build_cip_soc_prior()
    print(f"wrote {len(frame)} CIP-SOC pairs to {CIP_PRIOR_PATH}")
    print(f"  distinct CIP codes: {frame['cip_code'].nunique()}")
    print(f"  distinct SOC codes: {frame['soc_code'].nunique()}")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
