"""Cross-release consistency assertions run before anything is built.

Why this module exists, concretely: an earlier attempt at this pipeline used
O*NET release 20.3, which carries the O*NET-SOC **2010** taxonomy. 262 of its
1,109 codes do not exist in the 2019 taxonomy. The join against the newer
occupation table therefore dropped roughly a quarter of the rows -- silently,
because a left join against a missing key produces nulls rather than an error.
Every assertion here exists to make that class of failure loud.

Each check raises :class:`ReleaseValidationError` with the offending codes
listed. The exploratory, one-off version of the code-overlap check lives in
``dev/compare_release_code_overlap.py``; this module is the enforcing version
that build scripts call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fetch_source_files import (
    read_all_occupations,
    read_bls_soc_structure,
    read_cip_soc_crosswalk,
    read_onet_readme,
    read_onet_table,
)

__all__ = [
    "ReleaseValidationError",
    "ValidationReport",
    "EXPECTED_RELEASE",
    "validate_onet_release",
    "validate_against_bls",
    "validate_all",
    "main",
]

EXPECTED_RELEASE = "30.3"
_ONET_SOC_CODE = re.compile(r"^\d{2}-\d{4}\.\d{2}$")
_SOC_CODE = re.compile(r"^\d{2}-\d{4}$")
_MAX_REPORTED = 15

CROSSWALK_SENTINEL_CODES = frozenset({"99-9999"})
"""Placeholders NCES uses for "no SOC match", not real occupations.

``99-9999`` is SOC-shaped but is not defined in the BLS structure file. It is a
sentinel, so it is excluded from the structural check here and dropped from the
CIP prior in :mod:`occupational_scrape.build_cip_soc_prior`.
"""


class ReleaseValidationError(AssertionError):
    """A cross-release consistency assertion failed."""


@dataclass
class ValidationReport:
    checks: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        self.checks.append(message)

    def __str__(self) -> str:
        return "\n".join(f"  ok  {check}" for check in self.checks)


def _sample(codes) -> str:
    ordered = sorted(codes)
    head = ", ".join(ordered[:_MAX_REPORTED])
    if len(ordered) > _MAX_REPORTED:
        head += f", ... (+{len(ordered) - _MAX_REPORTED} more)"
    return head


def validate_onet_release(report: ValidationReport | None = None) -> ValidationReport:
    """Assert the O*NET bundle is internally consistent and is release 30.3."""
    report = report or ValidationReport()

    readme = read_onet_readme()
    if readme and EXPECTED_RELEASE not in readme.splitlines()[0]:
        raise ReleaseValidationError(
            f"O*NET archive Read Me says {readme.splitlines()[0]!r}; "
            f"expected release {EXPECTED_RELEASE}. Wrong bundle is pinned."
        )
    report.record(f"O*NET archive identifies as release {EXPECTED_RELEASE}")

    occupation_data = read_onet_table("occupation_data")
    known = set(occupation_data["O*NET-SOC Code"])
    if not known:
        raise ReleaseValidationError("Occupation Data.txt is empty")

    malformed = {code for code in known if not _ONET_SOC_CODE.match(code)}
    if malformed:
        raise ReleaseValidationError(
            f"Occupation Data.txt has {len(malformed)} codes that are not "
            f"O*NET-SOC shaped (NN-NNNN.NN): {_sample(malformed)}"
        )
    report.record(f"Occupation Data.txt: {len(known)} well-formed O*NET-SOC codes")

    # The check that release 20.3 would have failed.
    for table in ("job_titles", "sample_of_reported_titles"):
        frame = read_onet_table(table)
        codes = set(frame["O*NET-SOC Code"])
        orphans = codes - known
        if orphans:
            raise ReleaseValidationError(
                f"{table}: {len(orphans)} of {len(codes)} codes are absent from "
                f"Occupation Data.txt of the same release. A join on these would "
                f"silently drop rows. Offending codes: {_sample(orphans)}"
            )
        report.record(f"{table}: all {len(codes)} distinct codes resolve in Occupation Data.txt")

    all_occupations = read_all_occupations()
    export_codes = set(all_occupations["Code"])
    if export_codes != known:
        only_export = export_codes - known
        only_db = known - export_codes
        raise ReleaseValidationError(
            "All_Occupations.csv and Occupation Data.txt describe different "
            f"releases: {len(only_export)} codes only in the export "
            f"({_sample(only_export)}); {len(only_db)} only in the database "
            f"({_sample(only_db)})."
        )
    if "Data-level" not in all_occupations.columns:
        raise ReleaseValidationError(
            "All_Occupations.csv is missing the Data-level column, which is the "
            "only signal distinguishing residual 'All Other' buckets."
        )
    residual = all_occupations["Data-level"].isna().sum()
    report.record(
        f"All_Occupations.csv: code set identical to Occupation Data.txt; "
        f"{residual} residual (blank Data-level) codes flagged"
    )
    return report


def validate_against_bls(report: ValidationReport | None = None) -> ValidationReport:
    """Assert every O*NET code rolls up to a SOC code the BLS file actually defines."""
    report = report or ValidationReport()

    structure = read_bls_soc_structure()
    malformed = set(structure.loc[~structure["code"].str.match(_SOC_CODE), "code"])
    if malformed:
        raise ReleaseValidationError(
            f"BLS structure file has {len(malformed)} codes that are not SOC shaped "
            f"(NN-NNNN): {_sample(malformed)}"
        )
    detailed = set(structure.loc[structure["level"] == "detailed", "code"])
    report.record(
        f"BLS SOC 2018 structure: {len(structure)} nodes "
        f"({len(detailed)} detailed occupations)"
    )

    onet_codes = set(read_onet_table("occupation_data")["O*NET-SOC Code"])
    unmatched = {code for code in onet_codes if code.split(".")[0] not in detailed}
    if unmatched:
        raise ReleaseValidationError(
            f"{len(unmatched)} O*NET-SOC codes do not roll up to a detailed SOC "
            f"occupation in the BLS 2018 structure file. This is the signature of a "
            f"taxonomy-year mismatch between the O*NET release and the SOC release. "
            f"Offending codes: {_sample(unmatched)}"
        )
    report.record(
        f"all {len(onet_codes)} O*NET-SOC codes roll up to a detailed SOC 2018 occupation"
    )

    crosswalk_socs = set(read_cip_soc_crosswalk()["soc_code"].dropna())
    known_socs = set(structure["code"])
    stray = (
        {code for code in crosswalk_socs if _SOC_CODE.match(code)}
        - known_socs
        - CROSSWALK_SENTINEL_CODES
    )
    if stray:
        raise ReleaseValidationError(
            f"CIP-SOC crosswalk references {len(stray)} SOC codes absent from the BLS "
            f"2018 structure file: {_sample(stray)}"
        )
    report.record(f"CIP-SOC crosswalk: all {len(crosswalk_socs)} SOC codes defined in BLS 2018")
    return report


def validate_all() -> ValidationReport:
    report = ValidationReport()
    validate_onet_release(report)
    validate_against_bls(report)
    return report


def main() -> None:
    report = validate_all()
    print("Source release validation passed:")
    print(report)


if __name__ == "__main__":
    main()
