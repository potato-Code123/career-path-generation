"""Title normalization shared by index construction and query-time lookup.

The same function must run on both sides or the index is unreachable: a title
normalized one way at build time and another way at query time simply never
matches. Nothing here may depend on configuration that can drift between the
two call sites.

The normalization, in order:

1. Split off parenthetical content. O*NET publishes acronyms as
   ``Full Name (ACRONYM)`` -- the parenthetical is indexed as its own key rather
   than discarded, so ``FPGA`` reaches the same code as
   ``Field Programmable Gate Array (FPGA)``.
2. Casefold.
3. Delete intra-word punctuation (apostrophes, periods) without leaving a gap,
   so ``bachelor's`` -> ``bachelors`` rather than ``bachelor s``.
4. Replace every other non-alphanumeric character with a space, except hyphens
   that sit between two alphanumerics. ``hardware/software`` becomes two tokens
   rather than the single nonsense token ``hardwaresoftware``.
5. Collapse whitespace.
6. Strip a trailing ``s`` from a token only when what remains is >= 4 chars and
   the token does not already end in ``ss``, so ``engineers`` -> ``engineer``
   while ``gas``, ``ics`` and ``access`` survive intact.

Step 6 is deliberately crude. It is applied identically on both sides, so it is
symmetric even where it is not linguistically correct (``physics`` -> ``physic``
on both sides still matches). Correctness of the linguistics matters less than
the guarantee that the same input always produces the same key.

The ``ss`` clause is not cosmetic: it is what makes the function **idempotent**.
See :func:`_strip_plural`.
"""

from __future__ import annotations

import re

__all__ = [
    "normalize_title",
    "normalization_variants",
    "split_parentheticals",
    "MIN_STEM_LENGTH",
]

MIN_STEM_LENGTH = 4
"""A trailing ``s`` is stripped only when the remaining token is at least this long."""

_PARENTHETICAL = re.compile(r"\(([^()]*)\)")
_DELETED_PUNCTUATION = re.compile(r"['‘’ʼ.]")
_INTERNAL_HYPHEN = re.compile(r"(?<=[a-z0-9])-(?=[a-z0-9])")
_HYPHEN_PLACEHOLDER = "\x00"
_NON_ALNUM = re.compile(r"[^a-z0-9\x00]+")
_WHITESPACE = re.compile(r"\s+")


def split_parentheticals(raw: str) -> tuple[str, list[str]]:
    """Return the title with parentheticals removed, plus their raw contents.

    ``"Computer Numerically Controlled (CNC) Operators"`` yields
    ``("Computer Numerically Controlled  Operators", ["CNC"])``. Whitespace is
    left ragged here; :func:`normalize_title` collapses it.
    """
    inner = [match.group(1) for match in _PARENTHETICAL.finditer(raw)]
    outer = _PARENTHETICAL.sub(" ", raw)
    return outer, inner


def _strip_plural(token: str) -> str:
    """Strip one trailing ``s`` when the stem is long enough and is not itself ``s``-final.

    The ``ss`` guard is what makes normalization idempotent, and idempotency is
    load-bearing rather than cosmetic. Without it ``access`` -> ``acces`` ->
    ``acce``: the key stored in the index and the key produced by normalizing that
    key a second time differ, so any code path that normalizes an
    already-normalized string -- the fuzzy stage does exactly this -- would search
    for a key the index cannot contain. It also happens to stop the rule mangling
    ``process`` and ``address``.
    """
    if token.endswith("ss"):
        return token
    if len(token) > MIN_STEM_LENGTH and token.endswith("s"):
        return token[:-1]
    return token


def normalize_title(raw: str) -> str:
    """Normalize a job title to its index key.

    Parenthetical content is dropped. Use :func:`normalization_variants` when the
    parenthetical should also be searchable.
    """
    if raw is None:
        return ""
    outer, _ = split_parentheticals(str(raw))
    return _normalize_core(outer)


def _normalize_core(text: str) -> str:
    lowered = text.casefold()
    lowered = _DELETED_PUNCTUATION.sub("", lowered)
    # Protect hyphens flanked by alphanumerics, then blanket-replace the rest.
    protected = _INTERNAL_HYPHEN.sub(_HYPHEN_PLACEHOLDER, lowered)
    spaced = _NON_ALNUM.sub(" ", protected)
    spaced = spaced.replace(_HYPHEN_PLACEHOLDER, "-")
    collapsed = _WHITESPACE.sub(" ", spaced).strip()
    if not collapsed:
        return ""
    return " ".join(_strip_plural(token) for token in collapsed.split(" "))


def normalization_variants(raw: str) -> tuple[str, ...]:
    """Return every normalized key a single surface title should be indexed under.

    The first element is always the parenthetical-stripped form; any non-empty
    parenthetical contributes an additional key. Order is stable and duplicates
    are removed, so this is safe to use as a deterministic build-time expansion.
    """
    if raw is None:
        return ()
    outer, inner = split_parentheticals(str(raw))
    keys: list[str] = []
    for candidate in (outer, *inner):
        key = _normalize_core(candidate)
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)
