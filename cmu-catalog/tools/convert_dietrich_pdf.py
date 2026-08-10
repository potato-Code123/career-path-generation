"""Convert the Dietrich Gen Ed course list PDF into data/dietrich_gened_snapshot.json.

    python tools/convert_dietrich_pdf.py "path/to/Dietrich GenEd Course List.pdf"

The Dietrich workbook has data export disabled (`allowDataAccess: false`), and
its table is virtualised inside a fixed-height dashboard zone, so neither the
DOM nor the vizql session yields the full list. PDF export is the one export
Tableau still allows, and it renders the *entire* crosstab rather than the
visible window — 30 pages, 302 courses, 724 course-semester rows.

pypdf flattens the crosstab to one cell per line:

    03124                       <- course number, starts a course
    Modern Biology Laboratory   <- title, may wrap over several lines
    F25                         <- semester, starts a 4-line offering block
    Scientific Inquiry          <- category
    MCS                         <- college
    Biological Sciences         <- department
    F26                         <- next offering of the same course
    ...

A course number line starts a new course; a semester token starts an offering
block; anything else before the first semester is title continuation. Column
headers repeat on every page and are skipped.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

COURSE_CODE = re.compile(r"^\d{5}$")
SEMESTER = re.compile(r"^[FSMN]\d{2}$")
HEADERS = {"Course", "Long Title", "Semester", "Category", "College", "Department"}

# The campus column is rendered as its own block at the end of each page rather
# than inline with the rows — a run of identical values. Every row is
# Pittsburgh, so these lines are dropped.
CAMPUS_CODES = {"PIT"}

# College is the anchor that splits a row's tail into category and department.
# Category and department both wrap across lines, so a fixed-height block does
# not work: it swallows the next course number and silently drops courses.
COLLEGES = {"DC", "MCS", "SCS", "TSB", "CFA", "CMU", "CIT"}

# pypdf preserves the fi/fl ligatures Tableau embeds ("Scientiﬁc Inquiry").
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}


def read_lines(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    lines: list[str] = []
    for page in reader.pages:
        for line in (page.extract_text() or "").split("\n"):
            # Tableau's PDF uses tabs between words inside a cell.
            line = line.replace("\t", " ").strip()
            for ligature, replacement in LIGATURES.items():
                line = line.replace(ligature, replacement)
            if line and line not in HEADERS and line not in CAMPUS_CODES:
                lines.append(line)
    return lines


def parse(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    course: str | None = None
    title_parts: list[str] = []
    seen_semester = False
    index = 0

    while index < len(lines):
        line = lines[index]

        if COURSE_CODE.match(line):
            course = line
            title_parts = []
            seen_semester = False
            index += 1
            continue

        if SEMESTER.match(line) and course:
            # Collect this offering's tail: everything up to the next semester
            # or the next course number.
            end = index + 1
            while end < len(lines) and not (
                SEMESTER.match(lines[end]) or COURSE_CODE.match(lines[end])
            ):
                end += 1
            tail = lines[index + 1 : end]

            split = next((k for k, v in enumerate(tail) if v in COLLEGES), None)
            if split is None:
                # No college anchor: keep the row but leave the split unknown
                # rather than mis-assigning wrapped text to the wrong column.
                category, college, department = " ".join(tail).strip(), "", ""
            else:
                category = " ".join(tail[:split]).strip()
                college = tail[split]
                department = " ".join(tail[split + 1 :]).strip()

            rows.append(
                [course, " ".join(title_parts).strip(), line, category, college, department]
            )
            seen_semester = True
            index = end
            continue

        if course and not seen_semester:
            title_parts.append(line)

        index += 1

    return rows


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    pdf_path = Path(sys.argv[1])
    rows = parse(read_lines(pdf_path))

    courses = sorted({r[0] for r in rows})
    semesters = sorted({r[2] for r in rows})
    categories = sorted({r[3] for r in rows})

    payload = {
        "_comment": (
            "Dietrich approved general-education courses. Tableau data export is "
            "disabled on this workbook and its table is virtualised in a "
            "fixed-height dashboard zone, so this comes from the PDF export, "
            "which renders the whole crosstab. See extract_tableau_geneds.md."
        ),
        "source": "https://public.tableau.com/views/GeneralEducationPublicSearchTool/GenEdDashboard",
        "source_pdf": pdf_path.name,
        "columns": ["course_number", "title", "semester", "category", "college", "department"],
        "semesters": semesters,
        "categories": categories,
        "rows": rows,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "dietrich_gened_snapshot.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{len(rows)} rows | {len(courses)} courses | {len(categories)} categories")
    print(f"semesters: {', '.join(semesters)}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
