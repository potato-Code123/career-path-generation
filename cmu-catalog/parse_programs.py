"""Parse CourseLeaf program pages into degree requirement groups.

Requirements live in `<table class="sc_courselist">`. A table looks like:

    <table class="sc_courselist" width="100%">
    <td colspan="2">Computer Science Core (all of the following):</td>   <- caption
    <td class="hourscol">Units</td>
    <colgroup>...</colgroup>
    <tbody>
      <tr class="even firstrow">                                        <- course row
        <td class="codecol"><a class="bubblelink code">07-128</a></td>
        <td>First Year Immigration Course</td>
        <td class="hourscol">3</td>
      </tr>
      <tr class="orclass even">                                         <- alternative
        <td class="codecol orclass">or <a ...>21-242</a></td>
        <td colspan="2"> Matrix Theory</td>
      </tr>
      <tr class="odd">                                                  <- prose requirement
        <td colspan="2"><span class="courselistcomment">These electives...</span></td>
        <td class="hourscol">18</td>
      </tr>
      <tr class="listsum"><td colspan="2"></td><td class="hourscol">51</td></tr>
    </tbody>
    </table>

Note the caption `<td>` sits outside any `<tr>` — CourseLeaf emits malformed
markup there, so it is extracted separately rather than as a row.

Two things carry meaning that is easy to throw away:

  * The caption and surrounding headings hold the boolean semantics
    ("all of the following", "minimum 5 courses", "select two"). We keep them
    verbatim rather than trying to parse intent out of them.
  * Tables under a "Sample Course Sequence" heading are a suggested schedule,
    not a requirement. They repeat courses already required elsewhere, so
    counting them as requirements double-counts the degree. They are kept but
    flagged via `is_sample_sequence`.
"""

from __future__ import annotations

import html
import re

TABLE = re.compile(r'<table class="sc_courselist".*?</table>', re.S)
ROW = re.compile(r"<tr[^>]*>.*?</tr>", re.S)
ROW_CLASS = re.compile(r'<tr[^>]*class="([^"]*)"', re.S)
CELL = re.compile(r"<td[^>]*>.*?</td>", re.S)
CELL_CLASS = re.compile(r'<td[^>]*class="([^"]*)"')
CODE_LINK = re.compile(r'<a[^>]*class="bubblelink code"[^>]*>(.*?)</a>', re.S)
HOURS = re.compile(r'<td[^>]*class="hourscol"[^>]*>(.*?)</td>', re.S)
COMMENT = re.compile(r'<span class="courselistcomment[^"]*">(.*?)</span>', re.S)
HEADING = re.compile(r"<(h[1-4])[^>]*>(.*?)</\1>", re.S)
TAG = re.compile(r"<[^>]+>")
COURSE_CODE = re.compile(r"^\d{2}-\d{3}$")

SAMPLE_SEQUENCE = re.compile(r"sample\s+(course\s+)?sequence|sample\s+schedule", re.I)


# Zero-width and bidi marks appear in a few catalog headings. They break console
# output on cp1252 and are invisible noise in the data either way.
INVISIBLE = re.compile(r"[​-‏‪-‮﻿­]")


def _text(fragment: str) -> str:
    plain = TAG.sub(" ", fragment)
    plain = html.unescape(plain).replace("\xa0", " ")
    plain = INVISIBLE.sub("", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _units(fragment: str) -> float | None:
    match = HOURS.search(fragment)
    if not match:
        return None
    raw = _text(match.group(1))
    try:
        return float(raw)
    except ValueError:
        return None


def _caption(table_html: str) -> str | None:
    """The table's own header cell, which sits before <colgroup>."""
    head = table_html.split("<colgroup", 1)[0]
    cells = CELL.findall(head)
    for cell in cells:
        if "hourscol" in (CELL_CLASS.search(cell).group(1) if CELL_CLASS.search(cell) else ""):
            continue
        text = _text(cell)
        if text:
            return text
    return None


def _parse_rows(table_html: str) -> tuple[list[dict], float | None]:
    rows: list[dict] = []
    total_units: float | None = None

    for row_html in ROW.findall(table_html):
        class_match = ROW_CLASS.search(row_html)
        row_class = class_match.group(1) if class_match else ""

        if "listsum" in row_class:
            total_units = _units(row_html)
            continue

        cells = CELL.findall(row_html)
        if not cells:
            continue

        first_class = ""
        if CELL_CLASS.search(cells[0]):
            first_class = CELL_CLASS.search(cells[0]).group(1)

        codes = [_text(c) for c in CODE_LINK.findall(cells[0])] if cells else []
        codes = [c for c in codes if COURSE_CODE.match(c)]

        # "or 21-242" continuation of the preceding requirement.
        if "orclass" in first_class or "orclass" in row_class:
            if codes and rows and rows[-1]["kind"] == "course":
                title = _text(cells[1]) if len(cells) > 1 else ""
                rows[-1]["alternatives"].append({"course_id": codes[0], "title": title})
            continue

        if "codecol" in first_class and codes:
            title_cell = cells[1] if len(cells) > 1 else ""
            # A <br/> in the title cell separates the title from a parenthetical note.
            parts = re.split(r"<br\s*/?>", title_cell, maxsplit=1)
            title = _text(parts[0])
            note = _text(parts[1]) if len(parts) > 1 else None
            rows.append(
                {
                    "kind": "course",
                    "course_id": codes[0],
                    "title": title,
                    "note": note or None,
                    "units": _units(row_html),
                    "alternatives": [],
                }
            )
            continue

        # Prose requirement, e.g. "These electives can be from any SCS department...".
        comment = COMMENT.search(row_html)
        if comment:
            mentioned = [c for c in (_text(x) for x in CODE_LINK.findall(row_html))
                         if COURSE_CODE.match(c)]
            rows.append(
                {
                    "kind": "comment",
                    "text": _text(comment.group(1)),
                    "units": _units(row_html),
                    "mentioned_courses": mentioned,
                }
            )

    return rows, total_units


def parse_page(page_html: str, source_url: str = "") -> dict | None:
    """Parse one program page. Returns None if it carries no requirement tables."""
    if "sc_courselist" not in page_html:
        return None

    headings = [(m.start(), m.group(1), _text(m.group(2))) for m in HEADING.finditer(page_html)]

    title_match = re.search(r"<title>(.*?)</title>", page_html, re.S)
    program_name = _text(title_match.group(1)).split("<")[0].strip() if title_match else ""
    program_name = re.sub(r"\s*<?\s*Carnegie Mellon University\s*$", "", program_name).strip(" <")

    groups: list[dict] = []
    for match in TABLE.finditer(page_html):
        position = match.start()
        credential = None  # nearest preceding h2
        section = None  # nearest preceding h3/h4
        for start, level, text in headings:
            if start > position:
                break
            if level == "h2":
                credential, section = text, None
            elif level in ("h3", "h4"):
                section = text

        rows, total_units = _parse_rows(match.group(0))
        if not rows:
            continue

        context = " ".join(filter(None, [credential, section]))
        groups.append(
            {
                "credential": credential,
                "section": section,
                "caption": _caption(match.group(0)),
                "total_units": total_units,
                "is_sample_sequence": bool(SAMPLE_SEQUENCE.search(context)),
                "rows": rows,
            }
        )

    if not groups:
        return None

    return {
        "program_name": program_name,
        "source_url": source_url,
        "requirement_groups": groups,
    }


def flatten(programs: list[dict]) -> list[dict]:
    """One row per (program, group, course) for joining against courses.json.

    Sample-sequence groups are excluded: they restate courses required
    elsewhere and would double-count the degree.
    """
    flat: list[dict] = []
    for program in programs:
        for index, group in enumerate(program["requirement_groups"]):
            if group["is_sample_sequence"]:
                continue
            for row in group["rows"]:
                if row["kind"] != "course":
                    continue
                flat.append(
                    {
                        "program_name": program["program_name"],
                        "source_url": program["source_url"],
                        "credential": group["credential"],
                        "section": group["section"],
                        "group_caption": group["caption"],
                        "group_index": index,
                        "course_id": row["course_id"],
                        "units": row["units"],
                        "alternatives": [a["course_id"] for a in row["alternatives"]],
                    }
                )
    return flat
