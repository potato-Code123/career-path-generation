"""Parse CourseLeaf /courses/ pages into course records.

Each course on those pages is a block shaped like:

    <dl class="courseblock">
      <dt class="keepwithnext">07-120 Introduction to Software Construction</dt>
      <dd>Fall and Spring: 6 units<br />...description...<br />
          Prerequisite: <a class="bubblelink code" ...>15-112</a></dd>
    </dl>

We work block by block and split each <dd> on <br>, then classify segments by
their leading label. Page-wide regex is not safe here: description prose runs
straight into "Prerequisite:" with no separator in some blocks, so a global
pattern happily matches across a course boundary.

Note the prerequisite anchors point at /search/?P=..., which robots.txt
disallows. We read the anchor *text* and never follow the href.
"""

from __future__ import annotations

import html
import re

import prereqs

BLOCK = re.compile(r'<dl class="courseblock">(.*?)</dl>', re.S)
TITLE = re.compile(r'<dt[^>]*class="keepwithnext"[^>]*>(.*?)</dt>', re.S)
BODY = re.compile(r"<dd[^>]*>(.*?)</dd>", re.S)
BR = re.compile(r"<br\s*/?>", re.I)
TAG = re.compile(r"<[^>]+>")

CODE_AND_TITLE = re.compile(r"^(\d{2}-\d{3})\s+(.*)$", re.S)
# The offering line comes in three shapes:
#     "Fall and Spring: 6 units"   semesters + units
#     "All Semesters"              semesters only
#     "12 units"                   units only, no semester (e.g. 09-323)
OFFERING = re.compile(r"^(?P<semesters>[^:]+?)(?::\s*(?P<units>[\d.]+)\s*units?)?$", re.I)
UNITS_ONLY = re.compile(r"^(?P<units>[\d.]+)\s*units?$", re.I)

LABELS = {
    "prerequisite": "prerequisites",
    "prerequisites": "prerequisites",
    "corequisite": "corequisites",
    "corequisites": "corequisites",
    "course website": "website",
}
LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z ]{2,30}?)\s*:\s*(.*)$", re.S)


# Zero-width and bidi marks turn up in a few catalog strings; invisible noise.
INVISIBLE = re.compile(r"[​-‏‪-‮﻿­]")


def _text(fragment: str) -> str:
    """Strip tags and normalise whitespace and entities."""
    plain = TAG.sub(" ", fragment)
    plain = html.unescape(plain).replace("\xa0", " ")
    plain = INVISIBLE.sub("", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _split_offering(segment: str) -> tuple[list[str], float | None]:
    segment = segment.strip()

    units_only = UNITS_ONLY.match(segment)
    if units_only:
        return [], float(units_only.group("units"))

    match = OFFERING.match(segment)
    if not match:
        return [], None
    semesters = match.group("semesters").strip()
    units = match.group("units")
    parts = [p.strip() for p in re.split(r"\s+and\s+|\s*,\s*", semesters) if p.strip()]
    return parts, float(units) if units else None


def parse_page(page_html: str, source_url: str = "") -> list[dict]:
    """Parse every course block on one /courses/ page."""
    courses: list[dict] = []

    for block in BLOCK.findall(page_html):
        title_match = TITLE.search(block)
        if not title_match:
            continue
        heading = _text(title_match.group(1))
        code_match = CODE_AND_TITLE.match(heading)
        if not code_match:
            # Not a course block we recognise; skip rather than guess.
            continue
        code, name = code_match.group(1), code_match.group(2).strip()

        body_match = BODY.search(block)
        segments = BR.split(body_match.group(1)) if body_match else []

        semesters: list[str] = []
        units: float | None = None
        description_parts: list[str] = []
        fields: dict[str, str] = {}

        for index, segment in enumerate(segments):
            plain = _text(segment)
            if not plain:
                continue

            if index == 0:
                semesters, units = _split_offering(plain)
                if semesters or units is not None:
                    continue

            label_match = LABEL_RE.match(plain)
            if label_match:
                label = label_match.group(1).strip().lower()
                if label in LABELS:
                    fields[LABELS[label]] = label_match.group(2).strip()
                    continue

            description_parts.append(plain)

        # Some blocks glue the description straight onto "Prerequisite:" with no
        # <br>. Recover those by splitting the trailing description segment.
        if "prerequisites" not in fields and description_parts:
            tail = description_parts[-1]
            inline = re.search(r"(?:^|(?<=[.\s]))(Prerequisites?)\s*:\s*(.+)$", tail)
            if inline:
                fields["prerequisites"] = inline.group(2).strip()
                description_parts[-1] = tail[: inline.start()].strip()

        courses.append(
            {
                "course_id": code,
                "name": name,
                "units": units,
                "semesters": semesters,
                "description": " ".join(p for p in description_parts if p).strip(),
                "website": fields.get("website"),
                "prerequisites": prereqs.parse(fields.get("prerequisites")),
                "corequisites": prereqs.parse(fields.get("corequisites")),
                "source_url": source_url,
            }
        )

    return courses


def merge(records: list[dict]) -> list[dict]:
    """Collapse duplicates, which arise because crosslisted courses appear on
    more than one school's page. First occurrence wins for scalar fields; every
    source URL is retained so you can audit disagreements."""
    by_code: dict[str, dict] = {}
    for record in records:
        code = record["course_id"]
        existing = by_code.get(code)
        if existing is None:
            record = dict(record)
            record["source_urls"] = [record.pop("source_url")]
            by_code[code] = record
            continue
        url = record["source_url"]
        if url and url not in existing["source_urls"]:
            existing["source_urls"].append(url)
        # Prefer a record that actually carries prerequisites.
        if not existing["prerequisites"]["raw"] and record["prerequisites"]["raw"]:
            existing["prerequisites"] = record["prerequisites"]
        if not existing["description"] and record["description"]:
            existing["description"] = record["description"]
    return sorted(by_code.values(), key=lambda r: r["course_id"])
