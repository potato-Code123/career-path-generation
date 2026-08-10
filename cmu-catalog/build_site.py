"""Build the trimmed JSON the website loads.

    python build_site.py

Reads data/ (produced by build.py) and writes site/data/. The raw files total
~21 MB, most of it course descriptions and offerings for semesters the site
does not use, so everything is sliced down and descriptions are split into
their own file the page fetches lazily.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "data"

SEMESTER = "F26"

# Requirement groups state their quantifier in prose. These patterns cover the
# common phrasings; anything unmatched falls back to "any", and the raw caption
# is always kept so the UI can show the real rule rather than only our guess.
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
ALL_OF = re.compile(r"\ball of the following\b", re.I)
CHOOSE_N = re.compile(
    r"\b(?:select|choose|complete|take)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.I,
)
MINIMUM_N = re.compile(r"\bminimum\s+(?:of\s+)?(\d+)\s+courses?\b", re.I)
N_COURSES = re.compile(r"\((\d+)\s+courses?\)", re.I)


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def classify(*labels: str | None) -> dict:
    """Best-effort reading of a requirement group's quantifier."""
    blob = " ".join(filter(None, labels))
    if not blob:
        return {"kind": "any", "n": None}

    match = MINIMUM_N.search(blob) or N_COURSES.search(blob)
    if match:
        return {"kind": "choose", "n": int(match.group(1))}

    match = CHOOSE_N.search(blob)
    if match:
        token = match.group(1).lower()
        return {"kind": "choose", "n": WORD_NUMBERS.get(token, int(token) if token.isdigit() else 1)}

    if ALL_OF.search(blob):
        return {"kind": "all", "n": None}
    return {"kind": "any", "n": None}


def build_courses(courses: list[dict]) -> tuple[list[dict], dict[str, str]]:
    index: list[dict] = []
    descriptions: dict[str, str] = {}
    for course in courses:
        index.append(
            {
                "id": course["course_id"],
                "name": course["name"],
                "units": course["units"],
                "sem": course["semesters"],
                "pre": {
                    "raw": course["prerequisites"]["raw"],
                    "tree": course["prerequisites"]["tree"],
                    "courses": course["prerequisites"]["courses"],
                    "review": course["prerequisites"]["needs_review"],
                },
                "co": course["corequisites"]["courses"],
            }
        )
        if course["description"]:
            descriptions[course["course_id"]] = course["description"]
    return index, descriptions


def build_degrees(programs: list[dict]) -> list[dict]:
    degrees: list[dict] = []
    for program in programs:
        by_credential: dict[str, list[dict]] = {}
        for index, group in enumerate(program["requirement_groups"]):
            if group["is_sample_sequence"]:
                continue
            rows = [
                {
                    "id": row["course_id"],
                    "title": row["title"],
                    "units": row["units"],
                    "alt": [a["course_id"] for a in row["alternatives"]],
                }
                for row in group["rows"]
                if row["kind"] == "course"
            ]
            notes = [row["text"] for row in group["rows"] if row["kind"] == "comment"]
            if not rows and not notes:
                continue
            credential = group.get("credential") or program["program_name"]
            by_credential.setdefault(credential, []).append(
                {
                    "gid": index,
                    "section": group.get("section"),
                    "caption": group.get("caption"),
                    "units": group.get("total_units"),
                    "rule": classify(group.get("caption"), group.get("section")),
                    "courses": rows,
                    "notes": notes,
                }
            )

        for credential, groups in by_credential.items():
            course_count = sum(len(g["courses"]) for g in groups)
            if course_count == 0:
                continue
            degrees.append(
                {
                    "id": f"{program['program_name']}||{credential}",
                    "program": program["program_name"],
                    "credential": credential,
                    "url": program["source_url"],
                    "groups": groups,
                    "course_count": course_count,
                }
            )
    degrees.sort(key=lambda d: (d["program"], d["credential"]))
    return degrees


def build_offerings(offerings: list[dict]) -> list[dict]:
    rows = []
    for offering in offerings:
        if offering["semester"] != SEMESTER:
            continue
        rows.append(
            {
                "id": offering["course_id"],
                "name": offering["name"],
                "dept": offering["department"],
                "section": offering["section"],
                "days": offering["days"],
                "begin": offering["begin"],
                "end": offering["end"],
                "loc": offering["location"],
                "mode": offering["delivery_mode"],
                "units": offering["units"],
                "unitsRaw": offering["units_raw"],
                "mini": offering["is_mini"],
                "tba": offering["tba"],
            }
        )
    return rows


def build_geneds(geneds: list[dict], offered: set[str]) -> list[dict]:
    """Gen-ed categories, with each course flagged for whether it runs in F26.

    SCS and MCS publish no per-semester tagging, so "offered" is decided by
    whether the course appears in the F26 Schedule of Classes. CIT and Dietrich
    carry explicit semester lists, which are used directly when present.
    """
    out = []
    for category in geneds:
        courses = []
        for course in category["courses"]:
            semesters = course.get("semesters")
            runs = SEMESTER in semesters if semesters else course["course_id"] in offered
            courses.append(
                {
                    "id": course["course_id"],
                    "title": course.get("title") or "",
                    "units": course.get("units"),
                    "dept": course.get("department"),
                    "college": course.get("college"),
                    "f26": bool(runs),
                }
            )
        out.append(
            {
                "school": category["school"],
                "section": category["section"],
                "category": category["category"],
                "listType": category["list_type"],
                "rule": category.get("unit_rule"),
                "desc": category.get("description"),
                "timeline": category.get("timeline"),
                "available": category["courses_available"],
                "reason": category.get("unavailable_reason"),
                "courses": courses,
                "f26Count": sum(1 for c in courses if c["f26"]),
            }
        )
    return out


def main() -> None:
    courses = load("courses.json")
    programs = load("programs.json")
    offerings = load("offerings.json")
    geneds = load("geneds.json")

    index, descriptions = build_courses(courses)
    degrees = build_degrees(programs)
    f26 = build_offerings(offerings)
    offered = {o["id"] for o in f26}
    gened_rows = build_geneds(geneds, offered)

    OUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "courses.json": index,
        "descriptions.json": descriptions,
        "degrees.json": degrees,
        "offerings.json": {"semester": SEMESTER, "rows": f26},
        "geneds.json": gened_rows,
    }
    for name, payload in payloads.items():
        path = OUT / name
        path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        print(f"  {name:20} {path.stat().st_size/1024:8.1f} KB")

    print(
        f"\n{len(index)} courses | {len(degrees)} degrees | "
        f"{len(f26)} {SEMESTER} offerings | {len(gened_rows)} gen-ed categories"
    )


if __name__ == "__main__":
    main()
