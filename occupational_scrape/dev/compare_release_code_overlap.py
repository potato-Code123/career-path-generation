"""Diagnostic: the exploratory version of the cross-release code-overlap check.

``src/occupational_scrape/validate_source_release.py`` is the enforcing version --
it raises and blocks the build. This script only *reports*, so it can be pointed
at any pair of releases to see how bad a mismatch would be before deciding what
to do about it. That is the difference in purpose: one is a gate, this is a
measurement.

Motivating history: an earlier attempt used O*NET release 20.3, which carries the
O*NET-SOC **2010** taxonomy. 262 of its 1,109 codes do not exist in the 2019
taxonomy, so the join silently dropped about a quarter of the data. Nothing
errored. This script is what would have caught it.

    PYTHONPATH=src python dev/compare_release_code_overlap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from occupational_scrape.fetch_source_files import (  # noqa: E402
    read_all_occupations,
    read_bls_soc_structure,
    read_cip_soc_crosswalk,
    read_onet_table,
)


def report(label: str, left: set[str], right: set[str], left_name: str, right_name: str) -> None:
    only_left = left - right
    only_right = right - left
    both = left & right
    total = len(left | right)
    print(f"\n--- {label}")
    print(f"  {left_name:<34} {len(left):>6}")
    print(f"  {right_name:<34} {len(right):>6}")
    print(f"  {'in both':<34} {len(both):>6}  ({len(both) / total:.1%} of the union)")
    print(f"  {'only in ' + left_name:<34} {len(only_left):>6}")
    if only_left:
        print(f"      {sorted(only_left)[:12]}")
    print(f"  {'only in ' + right_name:<34} {len(only_right):>6}")
    if only_right:
        print(f"      {sorted(only_right)[:12]}")


def main() -> None:
    occupation_data = set(read_onet_table("occupation_data")["O*NET-SOC Code"])
    job_titles = set(read_onet_table("job_titles")["O*NET-SOC Code"])
    reported = set(read_onet_table("sample_of_reported_titles")["O*NET-SOC Code"])
    export = set(read_all_occupations()["Code"])

    print("O*NET internal consistency (all should be 100% overlap for one release)")
    report("Job Titles vs Occupation Data", job_titles, occupation_data, "job_titles", "occupation_data")
    report(
        "Sample of Reported Titles vs Occupation Data",
        reported,
        occupation_data,
        "reported_titles",
        "occupation_data",
    )
    report("All_Occupations export vs Occupation Data", export, occupation_data, "export", "occupation_data")

    structure = read_bls_soc_structure()
    detailed = set(structure.loc[structure["level"] == "detailed", "code"])
    stems = {code.split(".")[0] for code in occupation_data}
    print("\n\nO*NET-SOC stems against the BLS SOC 2018 taxonomy")
    report("O*NET stems vs BLS detailed occupations", stems, detailed, "onet_stems", "bls_detailed")

    crosswalk = set(read_cip_soc_crosswalk()["soc_code"].dropna())
    print("\n\nCIP-SOC crosswalk against the BLS SOC 2018 taxonomy")
    report("crosswalk SOCs vs BLS nodes", crosswalk, set(structure["code"]), "crosswalk", "bls_all")
    print("\n  note: 99-9999 is the NCES 'no SOC match' sentinel, not a real occupation.")


if __name__ == "__main__":
    main()
