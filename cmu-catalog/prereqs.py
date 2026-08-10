"""Parse CourseLeaf prerequisite strings into boolean trees.

Real examples from the catalog:

    15-112
    15-300 or 07-300
    15-151 or 15-127 or 21-128
    18-345 and senior or graduate standing.

The last one is why this module refuses to be clever. Standard boolean
precedence reads it as "(18-345 and senior) or graduate standing", but a
human reads it as "18-345 and (senior or graduate standing)". Natural
language does not carry enough signal to resolve that, so we:

  * parse with standard precedence (and binds tighter than or),
  * keep the raw string on every record, and
  * set needs_review when the expression mixes and/or, or contains prose
    we could not resolve to a course code.

That leaves a small, explicitly-flagged set for a human to check instead of
a large set of silently-wrong trees.
"""

from __future__ import annotations

import re
from typing import Any

COURSE_CODE = re.compile(r"\b(\d{2}-\d{3})\b")

_TOKEN = re.compile(
    r"""
      (?P<lparen>\() | (?P<rparen>\))
    | \b(?P<and>and)\b | \b(?P<or>or)\b
    | (?P<course>\d{2}-\d{3})
    | (?P<prose>[^()]+?)(?=\(|\)|\band\b|\bor\b|\d{2}-\d{3}|$)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# "Min. grade C" is an inline qualifier attached to an individual course, not a
# suffix on the whole expression:
#     ( 03-250 Min. grade C or 02-250 Min. grade C) and 03-121 Min. grade C
# Stripping from the first occurrence to end-of-string silently drops every
# course after it — that cost 244 of 1563 expressions before this was split out.
# Remove the qualifiers in place and leave the structure intact.
_GRADE_QUALIFIER = re.compile(
    r"\bMin(?:imum)?\.?\s*grade\s*[A-Z][+-]?\b", re.IGNORECASE
)

# These genuinely do trail the expression.
_TRAILING_NOISE = re.compile(r"\s*Course\s+Website\s*:.*$", re.IGNORECASE | re.S)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for match in _TOKEN.finditer(text):
        kind = match.lastgroup
        value = (match.group(kind) or "").strip(" ,.;")
        if kind == "prose" and not value:
            continue
        tokens.append((kind, value))
    return tokens


class _Parser:
    """Recursive descent: expression := term ("or" term)*, term := atom ("and" atom)*."""

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self) -> Any:
        node = self.parse_or()
        return node

    def parse_or(self) -> Any:
        operands = [self.parse_and()]
        while self.peek() == "or":
            self.next()
            operands.append(self.parse_and())
        operands = [o for o in operands if o is not None]
        if not operands:
            return None
        return operands[0] if len(operands) == 1 else {"or": operands}

    def parse_and(self) -> Any:
        operands = [self.parse_atom()]
        while self.peek() == "and":
            self.next()
            operands.append(self.parse_atom())
        operands = [o for o in operands if o is not None]
        if not operands:
            return None
        return operands[0] if len(operands) == 1 else {"and": operands}

    def parse_atom(self) -> Any:
        kind = self.peek()
        if kind is None:
            return None
        if kind == "lparen":
            self.next()
            inner = self.parse_or()
            if self.peek() == "rparen":
                self.next()
            return inner
        if kind == "rparen":
            return None
        kind, value = self.next()
        if kind == "course":
            return {"course": value}
        if kind == "prose":
            return {"text": value} if value else None
        # A stray and/or with no left operand; skip it.
        return None


def _leaves(node: Any) -> list[dict]:
    if node is None:
        return []
    if "course" in node or "text" in node:
        return [node]
    key = "and" if "and" in node else "or"
    return [leaf for child in node[key] for leaf in _leaves(child)]


def parse(raw: str | None) -> dict:
    """Parse a prerequisite string into a structured record.

    Returns a dict with:
        raw            the original string, always
        tree           nested {"and"/"or": [...]} over {"course"} / {"text"} leaves
        courses        flat list of course codes mentioned, deduped, in order
        needs_review   True when the tree may not reflect intended grouping
        review_reason  why, when needs_review is set
    """
    if not raw or not raw.strip():
        return {
            "raw": raw or "",
            "tree": None,
            "courses": [],
            "needs_review": False,
            "review_reason": None,
        }

    cleaned = _TRAILING_NOISE.sub("", raw.strip())
    cleaned = _GRADE_QUALIFIER.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;,")
    tree = _Parser(_tokenize(cleaned)).parse()

    codes: list[str] = []
    for code in COURSE_CODE.findall(cleaned):
        if code not in codes:
            codes.append(code)

    lowered = cleaned.lower()
    mixed = re.search(r"\band\b", lowered) and re.search(r"\bor\b", lowered)
    prose = [leaf for leaf in _leaves(tree) if "text" in leaf]

    reason = None
    if mixed and "(" not in cleaned:
        reason = "mixes 'and'/'or' without parentheses; grouping is ambiguous"
    elif prose and not codes:
        reason = "no course codes found; requirement is prose only"
    elif prose:
        reason = "contains prose conditions alongside course codes"

    return {
        "raw": raw.strip(),
        "tree": tree,
        "courses": codes,
        "needs_review": reason is not None,
        "review_reason": reason,
    }
