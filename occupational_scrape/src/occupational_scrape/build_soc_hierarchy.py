"""Build the SOC backoff ladder: ``data/processed/soc_hierarchy.parquet``.

One row per node, five levels deep::

    onet_detail  17-2061.00
    detailed     17-2061
    broad        17-2060
    minor        17-2000
    major        17-0000

Deriving parents by chopping the code string is the obvious approach and it is
wrong often enough to matter. SOC 2018 has minor groups whose fourth digit is
non-zero -- ``15-1200`` (Computer Occupations) is a minor group, so string
decomposition of ``15-1221`` proposes ``15-1000``, which does not exist. Broad
groups routinely share a title with their single detailed occupation
(``11-1010`` / ``11-1011`` are both "Chief Executives"), and residual "All Other"
codes sit at odd places in the tree.

So decomposition only ever *proposes*. Every node and every edge is then
validated against the BLS structure file, and where the two disagree the BLS
file wins. Each disagreement is written to
``dev/reports/soc_hierarchy_conflicts.csv`` so the discrepancies are inspectable
rather than merely absorbed.

Parent resolution rule: a coarser node ``P`` is an ancestor of ``C`` when ``P``'s
six digits with trailing zeros removed form a prefix of ``C``'s six digits. The
parent is the most specific such ``P`` present in the BLS file. This handles
``15-1200`` correctly without special-casing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import PROCESSED_DIR, PROJECT_ROOT
from .fetch_source_files import read_bls_soc_structure, read_onet_table
from .validate_source_release import validate_against_bls, validate_onet_release

__all__ = [
    "LEVELS",
    "LEVEL_ORDER",
    "HIERARCHY_PATH",
    "CONFLICT_REPORT_PATH",
    "SocHierarchy",
    "build_hierarchy",
    "load_soc_hierarchy",
    "decompose_code",
    "main",
]

LEVELS = ("onet_detail", "detailed", "broad", "minor", "major")
LEVEL_ORDER = {"major": 0, "minor": 1, "broad": 2, "detailed": 3, "onet_detail": 4}

HIERARCHY_PATH = PROCESSED_DIR / "soc_hierarchy.parquet"
CONFLICT_REPORT_PATH = PROJECT_ROOT / "dev" / "reports" / "soc_hierarchy_conflicts.csv"


def _digits(code: str) -> str:
    """``"17-2061"`` -> ``"172061"``. O*NET suffixes are dropped."""
    return code.split(".")[0].replace("-", "")


def _significant_prefix(code: str) -> str:
    """The digits of a group code with trailing zeros removed.

    ``15-1200`` -> ``"1512"``; ``17-0000`` -> ``"17"``. This is what makes a
    group code a prefix-matcher for its descendants.
    """
    return _digits(code).rstrip("0")


def decompose_code(code: str) -> dict[str, str]:
    """Propose ancestors by string manipulation alone.

    Returned codes are *candidates*, not facts: they are compared against the BLS
    structure file and overruled by it. Kept as a named function because the
    comparison is the point -- see the conflict report.
    """
    digits = _digits(code)
    proposals: dict[str, str] = {}
    if "." in code:
        proposals["detailed"] = f"{digits[:2]}-{digits[2:]}"
    proposals["broad"] = f"{digits[:2]}-{digits[2:5]}0"
    proposals["minor"] = f"{digits[:2]}-{digits[2:3]}000"
    proposals["major"] = f"{digits[:2]}-0000"
    return proposals


@dataclass(frozen=True)
class SocHierarchy:
    """In-memory view of ``soc_hierarchy.parquet`` with ladder traversal."""

    frame: pd.DataFrame

    def __post_init__(self) -> None:
        parents = {
            str(code): (None if parent is None or pd.isna(parent) else str(parent))
            for code, parent in zip(self.frame["code"], self.frame["parent"])
        }
        children: dict[str, list[str]] = {code: [] for code in parents}
        for code, parent in parents.items():
            if parent is not None and parent in children:
                children[parent].append(code)
        object.__setattr__(self, "_parent_map", parents)
        object.__setattr__(self, "_children_map", children)
        object.__setattr__(
            self,
            "_level_map",
            {str(c): str(v) for c, v in zip(self.frame["code"], self.frame["level"])},
        )
        object.__setattr__(
            self,
            "_title_map",
            {str(c): str(v) for c, v in zip(self.frame["code"], self.frame["title"])},
        )

    def __contains__(self, code: str) -> bool:
        return code in self._parent_map  # type: ignore[attr-defined]

    def level(self, code: str) -> str:
        return self._level_map[code]  # type: ignore[attr-defined]

    def title(self, code: str) -> str:
        return self._title_map[code]  # type: ignore[attr-defined]

    def ancestors(self, code: str) -> list[str]:
        """The backoff ladder for ``code``, most specific first, including ``code``.

        ``ancestors("17-2061.00")`` is
        ``["17-2061.00", "17-2061", "17-2060", "17-2000", "17-0000"]``.
        Returns ``[]`` for an unknown code.
        """
        parents: dict[str, str | None] = self._parent_map  # type: ignore[attr-defined]
        if code not in parents:
            return []
        ladder = [code]
        seen = {code}
        current = parents[code]
        while current is not None:
            if current in seen:  # defensive: build_hierarchy already rejects cycles
                raise RuntimeError(f"cycle in SOC hierarchy at {current}")
            ladder.append(current)
            seen.add(current)
            current = parents.get(current)
        return ladder

    def descendants(self, code: str) -> set[str]:
        """Every node whose ladder passes through ``code`` (excluding ``code``)."""
        children: dict[str, list[str]] = self._children_map  # type: ignore[attr-defined]
        if code not in children:
            return set()
        found: set[str] = set()
        stack = list(children[code])
        while stack:
            current = stack.pop()
            if current in found:
                continue
            found.add(current)
            stack.extend(children.get(current, ()))
        return found


def build_hierarchy(*, write: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the hierarchy table and the decomposition-vs-BLS conflict report."""
    validate_onet_release()
    validate_against_bls()

    structure = read_bls_soc_structure()
    bls_nodes = {
        str(code): {"code": str(code), "title": str(title), "level": str(level)}
        for code, title, level in zip(structure["code"], structure["title"], structure["level"])
    }
    bls_node_count = len(bls_nodes)

    occupation_data = read_onet_table("occupation_data")
    onet_nodes = {
        str(code): {"code": str(code), "title": str(title), "level": "onet_detail"}
        for code, title in zip(occupation_data["O*NET-SOC Code"], occupation_data["Title"])
    }

    nodes: dict[str, dict] = {**bls_nodes, **onet_nodes}

    # Index BLS group codes by level so parent lookup is a prefix scan, not a guess.
    by_level: dict[str, list[tuple[str, str]]] = {level: [] for level in LEVEL_ORDER}
    for code, node in bls_nodes.items():
        by_level[node["level"]].append((_significant_prefix(code), code))

    conflicts: list[dict[str, str]] = []

    def bls_parent(code: str, level: str) -> str | None:
        """Most specific BLS node strictly coarser than ``level`` that contains ``code``."""
        digits = _digits(code)
        for candidate_level in ("detailed", "broad", "minor", "major"):
            if LEVEL_ORDER[candidate_level] >= LEVEL_ORDER[level]:
                continue
            matches = [
                candidate
                for prefix, candidate in by_level[candidate_level]
                if digits.startswith(prefix) and candidate != code
            ]
            if matches:
                # Longest significant prefix is the most specific containing group.
                matches.sort(key=lambda candidate: len(_significant_prefix(candidate)))
                if len(matches) > 1:
                    conflicts.append(
                        {
                            "code": code,
                            "level": level,
                            "relation": candidate_level,
                            "decomposed_code": "",
                            "bls_code": matches[-1],
                            "reason": (
                                "multiple BLS groups at this level contain the code; "
                                f"took the most specific of {sorted(matches)}"
                            ),
                        }
                    )
                return matches[-1]
        return None

    parents: dict[str, str | None] = {}
    for code, node in sorted(nodes.items()):
        level = node["level"]
        if level == "major":
            parents[code] = None
            continue

        if level == "onet_detail":
            # An O*NET code's parent is its own SOC stem by construction; the
            # release validator has already proved the stem is a real detailed
            # occupation, so this needs no prefix search.
            stem = code.split(".")[0]
            parents[code] = stem
            if stem not in bls_nodes:
                conflicts.append(
                    {
                        "code": code,
                        "level": level,
                        "relation": "detailed",
                        "decomposed_code": stem,
                        "bls_code": "",
                        "reason": "O*NET stem absent from BLS structure file",
                    }
                )
            continue

        resolved = bls_parent(code, level)
        parents[code] = resolved

        # Compare against what naive string decomposition would have claimed.
        proposals = decompose_code(code)
        # Compare against the rung immediately above this node -- detailed against
        # its proposed broad group, broad against its proposed minor group, and so
        # on. Comparing against a further-away rung would report a "conflict" on
        # every node in the taxonomy.
        coarser = [
            candidate_level
            for candidate_level in ("detailed", "broad", "minor", "major")
            if LEVEL_ORDER[candidate_level] < LEVEL_ORDER[level]
        ]
        expected_level = coarser[0] if coarser else None
        if expected_level is not None:
            proposed = proposals.get(expected_level)
            if proposed is not None and proposed != resolved:
                if proposed not in bls_nodes:
                    reason = (
                        f"string decomposition proposed {proposed}, which the BLS "
                        f"structure file does not define"
                    )
                else:
                    reason = (
                        f"string decomposition proposed {proposed}, BLS placement is "
                        f"{resolved}"
                    )
                conflicts.append(
                    {
                        "code": code,
                        "level": level,
                        "relation": expected_level,
                        "decomposed_code": proposed or "",
                        "bls_code": resolved or "",
                        "reason": reason,
                    }
                )

        if resolved is None:
            raise RuntimeError(
                f"{code} ({level}) has no parent in the BLS structure file. "
                "The ladder would terminate early for every descendant."
            )

    frame = pd.DataFrame(
        [
            {
                "code": code,
                "title": nodes[code]["title"],
                "level": nodes[code]["level"],
                "parent": parents[code],
            }
            for code in sorted(nodes)
        ]
    ).astype({"code": "string", "title": "string", "level": "string", "parent": "string"})

    _assert_well_formed(frame, bls_node_count)

    conflict_frame = pd.DataFrame(
        conflicts,
        columns=["code", "level", "relation", "decomposed_code", "bls_code", "reason"],
    )

    if write:
        HIERARCHY_PATH.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(HIERARCHY_PATH, index=False)
        CONFLICT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        conflict_frame.to_csv(CONFLICT_REPORT_PATH, index=False)

    return frame, conflict_frame


def _assert_well_formed(frame: pd.DataFrame, bls_node_count: int) -> None:
    codes = set(frame["code"])

    orphans = {
        row.code
        for row in frame.itertuples()
        if row.level != "major" and (pd.isna(row.parent) or row.parent not in codes)
    }
    if orphans:
        raise RuntimeError(f"{len(orphans)} non-major nodes have a missing parent: {sorted(orphans)[:10]}")

    parents = dict(zip(frame["code"], frame["parent"]))
    for code in codes:
        seen = {code}
        current = parents[code]
        while current is not None and not pd.isna(current):
            if current in seen:
                raise RuntimeError(f"cycle in SOC hierarchy involving {code}")
            seen.add(current)
            current = parents.get(current)

    derived_bls = int((frame["level"] != "onet_detail").sum())
    if derived_bls != bls_node_count:
        raise RuntimeError(
            f"hierarchy holds {derived_bls} BLS-level nodes but the BLS structure file "
            f"defines {bls_node_count}"
        )


def load_soc_hierarchy(path: Path | None = None) -> SocHierarchy:
    """Load the built hierarchy. Raises if it has not been built yet."""
    target = path or HIERARCHY_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"{target} does not exist. Run build_soc_hierarchy.py first."
        )
    return SocHierarchy(pd.read_parquet(target))


def main() -> None:
    frame, conflicts = build_hierarchy()
    counts = frame["level"].value_counts()
    print(f"wrote {len(frame)} nodes to {HIERARCHY_PATH}")
    for level in LEVELS:
        print(f"  {level:<12} {counts.get(level, 0)}")
    print(f"wrote {len(conflicts)} decomposition conflicts to {CONFLICT_REPORT_PATH}")


if __name__ == "__main__":
    main()
