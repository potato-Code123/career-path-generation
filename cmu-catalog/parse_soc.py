"""Parse the CMU Schedule of Classes (SOC).

Two endpoints, with very different shapes and costs:

  search        POST, one request per semester with DEPT=All. Returns a flat
                table with **one row per course**, not per section — 3,382 rows
                for 3,327 distinct courses in Fall 2026. It is an offerings
                index: which courses run when, and the primary meeting time.

  courseDetails GET, one request per (course, semester). Returns the full
                lecture/recitation hierarchy, cross-listings, reservations and
                description. ~3,300 requests per semester, so opt-in only.

Neither endpoint publishes instructors. That was an open question from the
planning stage and the answer is no: the search table has ten columns and none
is an instructor, and courseDetails contains no instructor field either. If the
tool needs instructors, the FCE dataset carries them — which is another reason
the uro-fce request matters.
"""

from __future__ import annotations

import html
import re

# With DEPT=All the page contains one table per department — 56 of them, all
# sharing id="search-results-table" (duplicate ids, but that's what's served).
# Each is preceded by an <h4 class="department-title">, which is the only place
# the department name appears.
TABLE = re.compile(r'<table[^>]*id="search-results-table".*?</table>', re.S)
DEPARTMENT_TITLE = re.compile(r'<h4[^>]*class="department-title"[^>]*>(.*?)</h4>', re.S)
# SOC opens <tbody> and never closes it — the table ends </tr></table>. Anchor
# on </thead> instead of trying to match a tbody that has no closing tag.
THEAD_END = re.compile(r"</thead>", re.I)
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
COURSE_LINK = re.compile(r"COURSE=(\d+)")
TAG = re.compile(r"<[^>]+>")
INVISIBLE = re.compile(r"[​-‏‪-‮﻿­]")

# CMU day letters. U is Sunday, which shows up in Doha sections where the
# teaching week runs Sunday-Thursday.
DAY_NAMES = {
    "U": "Sunday", "M": "Monday", "T": "Tuesday", "W": "Wednesday",
    "R": "Thursday", "F": "Friday", "S": "Saturday",
}

HEADER_CELL = re.compile(r"<th[^>]*>(.*?)</th>", re.S)

# Column order is NOT fixed: summer semesters insert a "Session" column
# ("summer", "summer one", "summer two") after Course Title, giving 11 columns
# instead of 10. Positional mapping silently shifted every field after it, so
# the mapping is derived from each table's own header row.
HEADER_TO_FIELD = {
    "course": "course",
    "course title": "name",
    "session": "session",
    "units": "units",
    "sec": "section",
    "section": "section",
    "mini": "mini",
    "days": "days",
    "begin": "begin",
    "end": "end",
    "teaching location": "location",
    "delivery mode": "delivery_mode",
}
REQUIRED_FIELDS = {"course", "name", "units", "section", "days", "begin", "end"}


def _text(fragment: str) -> str:
    plain = TAG.sub(" ", fragment)
    plain = html.unescape(plain).replace("\xa0", " ")
    plain = INVISIBLE.sub("", plain)
    return re.sub(r"\s+", " ", plain).strip()


def normalise_course_id(number: str) -> str | None:
    """SOC writes course numbers without a dash: 15112 -> 15-112.

    The catalog uses NN-NNN and FCE splits department and number into separate
    columns, so everything is normalised to NN-NNN on ingest.
    """
    digits = re.sub(r"\D", "", number or "")
    if len(digits) != 5:
        return None
    return f"{digits[:2]}-{digits[2:]}"


def _parse_days(raw: str) -> list[str]:
    if not raw or raw.upper() == "TBA":
        return []
    return [DAY_NAMES[c] for c in raw.upper() if c in DAY_NAMES]


UNITS_RANGE = re.compile(r"^\s*([\d.]+)\s*-\s*([\d.]+)\s*$")


def _parse_units(raw: str) -> tuple[float | None, float | None, float | None]:
    """Return (units, units_min, units_max).

    Variable-credit courses are common and are written several ways:
        "12"      fixed
        "3-18"    a range
        "0,36"    a set of allowed values
        "VAR"     variable, unspecified
    A fixed value sets all three; ranges and sets set only min/max.
    """
    value = (raw or "").strip()
    if not value:
        return None, None, None

    try:
        fixed = float(value)
        return fixed, fixed, fixed
    except ValueError:
        pass

    span = UNITS_RANGE.match(value)
    if span:
        return None, float(span.group(1)), float(span.group(2))

    numbers = [float(n) for n in re.findall(r"[\d.]+", value)]
    if numbers:
        return None, min(numbers), max(numbers)
    return None, None, None


def _clean_time(raw: str) -> str | None:
    value = _text(raw)
    return None if not value or value.upper() == "TBA" else value


def parse_search(page_html: str, semester: str) -> list[dict]:
    """Parse one semester's search results into offering rows.

    Handles the multi-department page: every results table is parsed, and each
    is attributed to the nearest preceding department heading.
    """
    departments = [(m.start(), _text(m.group(1))) for m in DEPARTMENT_TITLE.finditer(page_html)]

    offerings: list[dict] = []
    for table in TABLE.finditer(page_html):
        department = None
        for start, name in departments:
            if start > table.start():
                break
            department = name
        offerings.extend(_parse_table(table.group(0), semester, department))
    return offerings


def _column_fields(table_html: str) -> list[str | None]:
    """Map this table's columns to field names using its own header row."""
    return [
        HEADER_TO_FIELD.get(_text(cell).lower())
        for cell in HEADER_CELL.findall(table_html)
    ]


def _parse_table(table_html: str, semester: str, department: str | None) -> list[dict]:
    fields = _column_fields(table_html)
    if not REQUIRED_FIELDS.issubset({f for f in fields if f}):
        return []

    head = THEAD_END.search(table_html)
    body = table_html[head.end():] if head else table_html

    offerings: list[dict] = []
    for row_html in ROW.findall(body):
        cells = CELL.findall(row_html)
        if len(cells) != len(fields):
            continue

        values = {
            field: cell for field, cell in zip(fields, cells) if field is not None
        }
        link = COURSE_LINK.search(values["course"])
        number = link.group(1) if link else _text(values["course"])
        course_id = normalise_course_id(number)
        if not course_id:
            continue

        days_raw = _text(values["days"])
        units, units_min, units_max = _parse_units(_text(values["units"]))
        offerings.append(
            {
                "semester": semester,
                "course_id": course_id,
                "department": department,
                "session": _text(values["session"]) or None if "session" in values else None,
                "name": _text(values["name"]),
                "units": units,
                "units_min": units_min,
                "units_max": units_max,
                "units_raw": _text(values["units"]),
                "section": _text(values["section"]),
                "is_mini": _text(values.get("mini", "")).upper() == "Y",
                "days": _parse_days(days_raw),
                "days_raw": days_raw,
                "begin": _clean_time(values["begin"]),
                "end": _clean_time(values["end"]),
                "tba": days_raw.upper() == "TBA",
                "location": _text(values.get("location", "")),
                "delivery_mode": _text(values.get("delivery_mode", "")),
            }
        )
    return offerings


# --- course detail pages -------------------------------------------------

FIELD_LABELS = (
    "Prerequisites", "Corequisites", "Cross-Listed Courses",
    "Notes", "Special Permission Required", "Description",
)

ANY_TABLE = re.compile(r"<table[^>]*>.*?</table>", re.S)

# The detail page's section table opens with a blank header column, and the page
# also carries a reservations table whose headers are Section/Restriction. Both
# are handled by header-driven mapping rather than fixed positions.
DETAIL_HEADER_TO_FIELD = {
    "units": "units",
    "section": "section",
    "mini": "mini",
    "days": "days",
    "begin": "begin",
    "end": "end",
    "teaching location": "location",
    "restriction": "restriction",
}
SECTION_TABLE_FIELDS = {"units", "section", "days", "begin", "end"}


def parse_details(page_html: str, course_id: str, semester: str) -> dict:
    """Parse a courseDetails page: full section list plus catalog-ish fields.

    Sections are flat in the source; lectures are rows whose section label looks
    like "Lec N" and the single-letter rows that follow belong to the preceding
    lecture. That parent link is reconstructed here because the source only
    encodes it by ordering.
    """
    sections: list[dict] = []
    restrictions: list[dict] = []
    current_lecture: str | None = None

    for table in ANY_TABLE.finditer(page_html):
        table_html = table.group(0)
        fields = [
            DETAIL_HEADER_TO_FIELD.get(_text(cell).lower())
            for cell in HEADER_CELL.findall(table_html)
        ]
        present = {f for f in fields if f}
        if not present:
            continue

        head = THEAD_END.search(table_html)
        body = table_html[head.end():] if head else table_html
        is_section_table = SECTION_TABLE_FIELDS.issubset(present)

        for row_html in ROW.findall(body):
            cells = CELL.findall(row_html)
            if len(cells) != len(fields):
                continue
            values = {f: _text(c) for f, c in zip(fields, cells) if f}

            if not is_section_table:
                if values.get("section") and values.get("restriction"):
                    restrictions.append(
                        {"section": values["section"], "restriction": values["restriction"]}
                    )
                continue

            label = values.get("section", "")
            if not label:
                continue

            is_lecture = bool(re.match(r"^(Lec|Lecture)\b", label, re.I))
            if is_lecture:
                current_lecture = label

            days_raw = values.get("days", "")
            tba = days_raw.upper() == "TBA"
            sections.append(
                {
                    "section": label,
                    "kind": "lecture" if is_lecture else "section",
                    "parent_lecture": None if is_lecture else current_lecture,
                    "units": _parse_units(values.get("units", ""))[0],
                    "units_raw": values.get("units") or None,
                    "is_mini": values.get("mini", "").upper() == "Y",
                    "days": _parse_days(days_raw),
                    "days_raw": days_raw,
                    "begin": None if tba else values.get("begin") or None,
                    "end": None if tba else values.get("end") or None,
                    "location": values.get("location", ""),
                }
            )

    plain = _text(page_html)
    fields: dict[str, str | None] = {}
    for index, label in enumerate(FIELD_LABELS):
        match = re.search(
            rf"{re.escape(label)}\s*:?\s*(.*?)(?=\s*(?:{'|'.join(re.escape(x) for x in FIELD_LABELS)})\s*:?|\s*Reservations\b|$)",
            plain,
        )
        value = match.group(1).strip() if match else ""
        fields[label.lower().replace(" ", "_")] = value or None

    crosslisted = []
    if fields.get("cross-listed_courses") or fields.get("cross_listed_courses"):
        raw = fields.get("cross_listed_courses") or ""
        crosslisted = [
            normalise_course_id(x) for x in re.findall(r"\b\d{2}-?\d{3}\b", raw)
        ]
        crosslisted = [c for c in crosslisted if c and c != course_id]

    return {
        "course_id": course_id,
        "semester": semester,
        "sections": sections,
        "restrictions": restrictions,
        "prerequisites_raw": _normalise_codes(_none_if_none(fields.get("prerequisites"))),
        "corequisites_raw": _normalise_codes(_none_if_none(fields.get("corequisites"))),
        "crosslisted": crosslisted,
        "notes": _none_if_none(fields.get("notes")),
        "special_permission_required": (
            (fields.get("special_permission_required") or "").strip().lower().startswith("yes")
        ),
        "description": fields.get("description"),
    }


def _normalise_codes(value: str | None) -> str | None:
    """SOC writes prerequisite codes without dashes, e.g.
    "(15210) and (21241) and (15251 or 21228)". Rewrite to NN-NNN so the string
    can be fed to prereqs.parse() and compared against the catalog's version.
    """
    if not value:
        return None
    return re.sub(r"\b(\d{2})(\d{3})\b", r"\1-\2", value)


def _none_if_none(value: str | None) -> str | None:
    """SOC writes the literal string 'None' for empty fields."""
    if value is None:
        return None
    return None if value.strip().lower() in ("", "none") else value.strip()
