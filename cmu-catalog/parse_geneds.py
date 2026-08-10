"""General education requirements, per school.

Four schools, four completely different publishing mechanisms:

  SCS       www.cs.cmu.edu — the visible tables are empty in the HTML and are
            populated from JSON endpoints under /rs-datasources/data-source/.
            Those endpoints are used directly, so this is clean typed data.

  MCS       the CourseLeaf catalog page, in ordinary sc_courselist tables.
            Already captured by parse_programs; re-exposed here by school.

  CIT       engineering.cmu.edu carries the category names and unit rules in
            prose; the approved-course list is an embedded Tableau Public
            workbook. It renders its whole table into the DOM, so the rows are
            extracted with a browser into data/cit_gened_snapshot.json.

  Dietrich  the curriculum page publishes categories, unit rules and timelines
            as plain HTML tables. The course lists are a second Tableau
            workbook with data export disabled and a virtualised table, so the
            source is its PDF export, converted by
            tools/convert_dietrich_pdf.py into
            data/dietrich_gened_snapshot.json. Pittsburgh campus.

Both snapshots are optional: if a file is missing the categories still build,
just without courses. See tools/extract_tableau_geneds.md to refresh them.

Every record carries `courses_available` and `list_type`, so a consumer can
tell "no courses qualify" from "the list is not published as data", and can
tell an approved list from an exclusion list.
"""

from __future__ import annotations

import html
import json
import re

import fetch

from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

SCS_PAGES = {
    "Humanities and Arts": "https://www.cs.cmu.edu/education/undergraduate/humanities-and-arts-requirements",
    "Science and Engineering": "https://www.cs.cmu.edu/education/undergraduate/science-and-engineering-reqs1",
}
CIT_PAGE = "https://engineering.cmu.edu/education/undergraduate-studies/curriculum/general-education/index.html"
DIETRICH_PAGE = "https://www.cmu.edu/dietrich/gened/"
CIT_TABLEAU = "https://public.tableau.com/views/COECourseSearch/CourseSearch"

DATASOURCE = re.compile(r'url:"(https://www\.cs\.cmu\.edu/rs-datasources/data-source/[^"]+)"')

# Not every SCS list is a list of courses that *satisfy* a requirement. Some
# enumerate courses recently added, and some enumerate courses that explicitly
# do NOT count ("Deletions", "Non-Qualifying Courses"). Treating those as
# approved would invert their meaning, so every category is tagged.
LIST_TYPES = (
    (re.compile(r"deletion|non-?qualify|not qualify|excluded", re.I), "excluded"),
    (re.compile(r"addition", re.I), "added"),
)


def _list_type(*labels: str | None) -> str:
    blob = " ".join(filter(None, labels))
    for pattern, kind in LIST_TYPES:
        if pattern.search(blob):
            return kind
    return "approved"


HEADING = re.compile(r"<h([1-4])[^>]*>(.*?)</h\1>", re.S)
TAG = re.compile(r"<[^>]+>")
INVISIBLE = re.compile(r"[​-‏‪-‮﻿­]")
COURSE_CODE = re.compile(r"^\d{2}-\d{3}$")


def _text(fragment: str) -> str:
    plain = TAG.sub(" ", fragment)
    plain = html.unescape(plain).replace("\xa0", " ")
    plain = INVISIBLE.sub("", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _rows_from_datasource(payload: str) -> list[dict]:
    """The endpoint returns {"cols":[{"n": name}], "data":[[...], ...]}."""
    document = json.loads(payload)
    names = [c.get("n", "") for c in document.get("cols", [])]
    rows: list[dict] = []
    for record in document.get("data", []):
        values = dict(zip(names, record))
        course_id = (values.get("course-number") or "").strip()
        if not COURSE_CODE.match(course_id):
            continue
        units = (values.get("units") or "").strip()
        try:
            units_value = float(units)
        except ValueError:
            units_value = None
        rows.append(
            {
                "course_id": course_id,
                "title": (values.get("title") or "").strip(),
                "units": units_value,
                "units_raw": units,
            }
        )
    return rows


def scs(refresh: bool = False) -> list[dict]:
    """SCS categories, each backed by one JSON data source.

    The category name is the nearest preceding heading on the page, matching how
    the requirement tables are labelled visually.
    """
    categories: list[dict] = []

    for section, url in SCS_PAGES.items():
        page = fetch.fetch(url, refresh=refresh)
        headings = [(m.start(), _text(m.group(2))) for m in HEADING.finditer(page)]

        for match in DATASOURCE.finditer(page):
            name = None
            for start, text in headings:
                if start > match.start():
                    break
                name = text

            source_url = match.group(1)
            slug = source_url.rsplit("/", 1)[-1]
            payload = fetch.fetch(source_url, refresh=refresh)
            courses = _rows_from_datasource(payload)

            categories.append(
                {
                    "school": "SCS",
                    "section": section,
                    "category": name or slug,
                    "slug": slug,
                    "list_type": _list_type(name, slug),
                    "courses": courses,
                    "course_count": len(courses),
                    "courses_available": True,
                    "source_url": source_url,
                    "page_url": url,
                }
            )

    return categories


def mcs(programs: list[dict]) -> list[dict]:
    """MCS gen eds come from the catalog page already parsed in step 2."""
    program = next(
        (p for p in programs if p["program_name"] == "Mellon College of Science"), None
    )
    if program is None:
        return []

    categories: list[dict] = []
    for group in program["requirement_groups"]:
        if not (group.get("credential") or "").lower().startswith("general education"):
            continue
        courses = [
            {
                "course_id": row["course_id"],
                "title": row["title"],
                "units": row["units"],
                "units_raw": None,
            }
            for row in group["rows"]
            if row["kind"] == "course"
        ]
        notes = [row["text"] for row in group["rows"] if row["kind"] == "comment"]
        categories.append(
            {
                "school": "MCS",
                "section": group.get("section"),
                "category": group.get("caption") or group.get("section"),
                "slug": None,
                "list_type": _list_type(group.get("caption"), group.get("section")),
                "courses": courses,
                "course_count": len(courses),
                "courses_available": True,
                "notes": notes or None,
                "source_url": program["source_url"],
                "page_url": program["source_url"],
            }
        )
    return categories


def _cit_courses_by_category() -> dict[str, list[dict]]:
    """Approved CIT courses, grouped by category, from the Tableau snapshot.

    The workbook is only reachable through a browser (see the module docstring
    and tools/extract_cit_tableau.md), so the rows are committed as a snapshot
    rather than fetched at build time. Missing snapshot is not an error — the
    categories still build, just without courses.
    """
    path = DATA_DIR / "cit_gened_snapshot.json"
    if not path.exists():
        return {}

    snapshot = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, dict[str, dict]] = {}
    for number, title, department, category, semester in snapshot["rows"]:
        course_id = f"{number[:2]}-{number[2:]}" if len(number) == 5 else number
        bucket = grouped.setdefault(category, {})
        entry = bucket.setdefault(
            course_id,
            {
                "course_id": course_id,
                "title": title,
                "units": None,
                "units_raw": None,
                "department": department,
                "semesters": [],
            },
        )
        if semester not in entry["semesters"]:
            entry["semesters"].append(semester)
    return {
        category: sorted(courses.values(), key=lambda c: c["course_id"])
        for category, courses in grouped.items()
    }


def _match_category(name: str, available: dict[str, list[dict]]) -> str | None:
    """Match a prose heading like "Peoples, Places, and Cultures (PPC)" to the
    Tableau category label "People, Places, and Cultures". The two spellings
    differ ("Peoples" vs "People"), so compare on the abbreviation-stripped stem.
    """
    def norm(value: str) -> str:
        value = re.sub(r"\s*\([A-Z&]{2,6}\)\s*$", "", value).lower()
        # The page and the workbook disagree on spelling: "Peoples" vs "People",
        # and "&" vs "and". Normalise both before comparing.
        value = value.replace("peoples", "people").replace("&", "and")
        return re.sub(r"\s+", " ", value).strip()

    stem = norm(name)
    for label in available:
        if norm(label) == stem:
            return label
    return None


def cit(refresh: bool = False) -> list[dict]:
    """CIT categories from the page, courses from the Tableau snapshot.

    The page carries the category names and unit rules in prose; the approved
    course list lives in an embedded Tableau Public workbook whose plain-HTTP
    export endpoints 404. The workbook does render a real table, so the rows are
    extracted with a browser and stored as a snapshot.
    """
    page = fetch.fetch(CIT_PAGE, refresh=refresh)
    start = page.find("General education categories")
    section = page[start:] if start >= 0 else page

    # Categories are list items, not headings:
    #   <li><strong>Peoples, Places, and Cultures (PPC)</strong>
    #       <ul><li>description</li><li>9 units from the PPC list</li></ul></li>
    # Unescape before matching the abbreviation, or "Innovation &amp;
    # Internationalization (I&I)" is missed.
    # The detail sits in a nested <ul> after the <strong>. A non-greedy match to
    # </li> stops at the *inner* item's close, so take everything up to the next
    # category heading instead and pull the list items out of that window.
    starts = [
        (m.start(), _text(m.group(1)))
        for m in re.finditer(r"<strong>(.*?)</strong>", section, re.S)
        if re.search(r"\([A-Z&]{2,6}\)\s*$", _text(m.group(1)))
    ]

    available = _cit_courses_by_category()
    used: set[str] = set()

    categories: list[dict] = []
    for index, (position, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else position + 2500
        window = section[position:end]
        detail = [_text(x) for x in re.findall(r"<li>(.*?)</li>", window, re.S)]
        detail = [d for d in detail if d and d != name]

        label = _match_category(name, available)
        courses = available.get(label, []) if label else []
        if label:
            used.add(label)

        categories.append(
            {
                "school": "CIT",
                "section": "General education categories",
                "category": name,
                "list_type": "approved",
                "description": detail[0] if detail else None,
                "unit_rule": next(
                    (d for d in detail if re.search(r"\d+\s*(-\s*\d+)?\s*units?", d, re.I)),
                    None,
                ),
                "slug": label,
                "courses": courses,
                "course_count": len(courses),
                "courses_available": bool(courses),
                "source_url": CIT_TABLEAU if courses else CIT_PAGE,
                "page_url": CIT_PAGE,
            }
        )

    # Tableau carries categories the prose page doesn't name as headings —
    # "General Education Elective" and "Free Elective Only". The latter is an
    # exclusion: those courses count as free electives, NOT toward gen ed.
    for label in sorted(set(available) - used):
        courses = available[label]
        categories.append(
            {
                "school": "CIT",
                "section": "General education categories",
                "category": label,
                "list_type": "excluded" if "free elective" in label.lower() else "approved",
                "description": None,
                "unit_rule": None,
                "slug": label,
                "courses": courses,
                "course_count": len(courses),
                "courses_available": True,
                "source_url": CIT_TABLEAU,
                "page_url": CIT_PAGE,
            }
        )
    return categories


DIETRICH_CURRICULUM = "https://www.cmu.edu/dietrich/gened/curriculum/index.html"
DIETRICH_TABLEAU = (
    "https://public.tableau.com/views/GeneralEducationPublicSearchTool/GenEdDashboard"
)


def _dietrich_courses_by_category() -> dict[str, list[dict]]:
    """Approved Dietrich courses, grouped by category, from the PDF snapshot.

    Data export is disabled on that workbook and its table is virtualised in a
    fixed-height dashboard zone, so the source is the PDF export, converted by
    tools/convert_dietrich_pdf.py. Pittsburgh campus only.
    """
    path = DATA_DIR / "dietrich_gened_snapshot.json"
    if not path.exists():
        return {}

    snapshot = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, dict[str, dict]] = {}
    for number, title, semester, category, college, department in snapshot["rows"]:
        course_id = f"{number[:2]}-{number[2:]}" if len(number) == 5 else number
        bucket = grouped.setdefault(category, {})
        entry = bucket.setdefault(
            course_id,
            {
                "course_id": course_id,
                "title": title,
                "units": None,
                "units_raw": None,
                "college": college,
                "department": department,
                "semesters": [],
            },
        )
        if semester not in entry["semesters"]:
            entry["semesters"].append(semester)
    return {
        category: sorted(courses.values(), key=lambda c: c["course_id"])
        for category, courses in grouped.items()
    }


def _match_dietrich(name: str, available: dict[str, list[dict]]) -> str | None:
    """The curriculum page and the workbook name categories slightly
    differently — "Additional Disciplines: Business, Design, or ..." on the page
    is just "Additional Disciplines" in the data. Compare on the part before
    any colon."""
    stem = re.sub(r"\s*:.*$", "", name).strip().lower()
    for label in available:
        if re.sub(r"\s*:.*$", "", label).strip().lower() == stem:
            return label
    return None


def dietrich(refresh: bool = False) -> list[dict]:
    """Dietrich's requirement categories, from the curriculum page's tables.

    The curriculum page carries five tables. The first is a units summary
    (Foundations 54, Disciplinary Perspectives 42, Special Seminars 18,
    Experiential Learning 1, Total 115). The rest are requirement tables with
    columns Category / Description / Units Required / Timeline / Course Options.

    The "Course Options" column is a pointer, not a list — nearly every row says
    "Search for Course Options", so the approved courses live behind a search
    tool rather than on the page. Categories, units and timelines are real data
    and are captured; `courses` stays empty by design.
    """
    page = fetch.fetch(DIETRICH_CURRICULUM, refresh=refresh)
    tables = re.findall(r"<table.*?</table>", page, re.S)
    available = _dietrich_courses_by_category()
    used: set[str] = set()

    summary: dict[str, str] = {}
    categories: list[dict] = []

    for table in tables:
        rows = [
            [_text(c) for c in re.findall(r"<t[dh].*?</t[dh]>", row, re.S)]
            for row in re.findall(r"<tr.*?</tr>", table, re.S)
        ]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue

        header = [c.lower() for c in rows[0]]
        if "category" not in header:
            # Units summary table: [label, units] pairs.
            for row in rows:
                cells = [c for c in row if c]
                if len(cells) >= 2:
                    summary[cells[-2]] = cells[-1]
            continue

        index = {name: position for position, name in enumerate(header)}
        for row in rows[1:]:
            if len(row) < len(header):
                continue
            name = row[index["category"]]
            if not name:
                continue
            options = row[index.get("course options", len(row) - 1)]
            label = _match_dietrich(name, available)
            courses = available.get(label, []) if label else []
            if label:
                used.add(label)

            record = {
                "school": "Dietrich",
                "section": "General Education Program",
                "category": name,
                "list_type": "approved",
                "description": row[index["description"]] if "description" in index else None,
                "unit_rule": row[index["units required"]] if "units required" in index else None,
                "timeline": row[index["timeline"]] if "timeline" in index else None,
                "course_options_pointer": options or None,
                "slug": label,
                "campus": "PIT" if courses else None,
                "courses": courses,
                "course_count": len(courses),
                "courses_available": bool(courses),
                "source_url": DIETRICH_TABLEAU if courses else DIETRICH_CURRICULUM,
                "page_url": DIETRICH_CURRICULUM,
            }
            if not courses:
                record["unavailable_reason"] = (
                    "No approved-course list published for this category; the "
                    f"page points to: {options}"
                )
            categories.append(record)

    for record in categories:
        record["program_units_summary"] = summary or None
    return categories


def build(programs: list[dict], refresh: bool = False) -> list[dict]:
    return scs(refresh) + mcs(programs) + cit(refresh) + dietrich(refresh)
