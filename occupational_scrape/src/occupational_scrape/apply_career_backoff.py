"""Narrow a resolution onto the curated ECE leaf set, recording how far it backed off.

``backoff_level`` is surfaced in the UI explanation panel, so it has to be a real
recorded value carried on the :class:`~occupational_scrape.resolve_career_title.Resolution`
from the moment the decision is made. Inferring it later from the returned codes
is not possible without re-running this logic, and re-running it against a
different leaf set or a different hierarchy would produce a different answer than
the one the student was actually shown.

The ladder, in order:

``leaf``        exactly one resolved code is in the ECE leaf set -- resolved
``ambiguous``   several are; all survivors are returned and the caller elicits.
                This function does **not** pick. Picking here would fabricate a
                choice the evidence does not support and bury it where no
                explanation panel could show it.
``broad`` / ``minor`` / ``major``
                none are, so walk each resolved code's ancestors upward and stop
                at the first level whose subtree contains at least one ECE leaf;
                return those leaves. The level named is where the walk stopped.
``cip_prior``   the ladder ran out. Fall back to the ECE leaves associated with
                the department's own CIP codes -- expert judgement, not observed
                placement. See :mod:`occupational_scrape.build_cip_soc_prior`.
``None``        no codes at all to work with; the caller elicits from scratch.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from . import CONFIG_DIR
from .build_cip_soc_prior import load_cip_soc_prior
from .build_soc_hierarchy import SocHierarchy, load_soc_hierarchy
from .resolve_career_title import Resolution

__all__ = [
    "LEAF_SET_PATH",
    "BACKOFF_LEVELS",
    "load_ece_leaf_set",
    "load_leaf_entries",
    "apply_backoff",
]

LEAF_SET_PATH = CONFIG_DIR / "ece_career_leaf_set.yaml"

BACKOFF_LEVELS = ("leaf", "ambiguous", "broad", "minor", "major", "cip_prior")

_WALK_LEVELS = ("broad", "minor", "major")


@lru_cache(maxsize=None)
def load_leaf_entries(path: str | None = None) -> tuple[dict, ...]:
    """The leaf set as written, including titles and justifications."""
    target = Path(path) if path else LEAF_SET_PATH
    raw = yaml.safe_load(target.read_text()) or []
    return tuple(dict(entry) for entry in raw)


@lru_cache(maxsize=None)
def load_ece_leaf_set(path: str | None = None) -> frozenset[str]:
    return frozenset(str(entry["code"]) for entry in load_leaf_entries(path))


def _leaves_under(hierarchy: SocHierarchy, node: str, leaves: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(leaves & hierarchy.descendants(node)))


def apply_backoff(
    resolution: Resolution,
    *,
    hierarchy: SocHierarchy | None = None,
    leaf_set: frozenset[str] | None = None,
    cip_prior_codes: frozenset[str] | None = None,
) -> Resolution:
    """Return a new :class:`Resolution` with ``codes`` narrowed and ``backoff_level`` set."""
    hierarchy = hierarchy if hierarchy is not None else load_soc_hierarchy()
    leaves = leaf_set if leaf_set is not None else load_ece_leaf_set()

    if not resolution.codes:
        return _with(resolution, (), None)

    # Direct intersection with the leaf set.
    survivors = tuple(code for code in resolution.codes if code in leaves)
    if len(survivors) == 1:
        return _with(resolution, survivors, "leaf")
    if len(survivors) > 1:
        return _with(resolution, tuple(sorted(survivors)), "ambiguous")

    # Walk upward. Each resolved code contributes its own ladder; the first level
    # at which *any* of them has ECE leaves beneath it terminates the walk, so a
    # resolution never backs off further than it has to.
    for level in _WALK_LEVELS:
        found: set[str] = set()
        for code in resolution.codes:
            for ancestor in hierarchy.ancestors(code):
                if ancestor not in hierarchy or hierarchy.level(ancestor) != level:
                    continue
                found.update(_leaves_under(hierarchy, ancestor, leaves))
        if found:
            return _with(resolution, tuple(sorted(found)), level)

    # Ladder exhausted.
    prior = (
        cip_prior_codes
        if cip_prior_codes is not None
        else frozenset(load_cip_soc_prior()["soc_code"].astype(str))
    )
    from_prior = sorted(
        leaf for leaf in leaves if leaf.split(".")[0] in prior
    )
    return _with(resolution, tuple(from_prior), "cip_prior")


def _with(resolution: Resolution, codes: tuple[str, ...], level: str | None) -> Resolution:
    return Resolution(
        input_raw=resolution.input_raw,
        codes=codes,
        stage=resolution.stage,
        candidates_considered=resolution.candidates_considered,
        backoff_level=level,
    )
