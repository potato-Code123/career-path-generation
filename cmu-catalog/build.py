"""Build the CMU course catalog + prerequisite graph.

    python build.py            # use cache where present
    python build.py --refresh  # re-fetch every page

Writes to data/:
    courses.json   one record per course, with parsed prerequisites
    prereq_graph.json  edge list + adjacency, for graph work
    report.json    counts and the needs-review queue
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fetch
import parse_courses
import parse_geneds
import parse_programs
import parse_soc

DATA_DIR = Path(__file__).parent / "data"


def build_courses(refresh: bool = False) -> list[dict]:
    urls = fetch.course_page_urls(refresh=refresh)
    print(f"{len(urls)} course pages in sitemap")

    records: list[dict] = []
    for index, url in enumerate(urls, start=1):
        page = fetch.fetch(url, refresh=refresh)
        found = parse_courses.parse_page(page, source_url=url)
        records.extend(found)
        print(f"  [{index:>2}/{len(urls)}] {len(found):>4} courses  {url}")

    courses = parse_courses.merge(records)
    print(f"\n{len(records)} blocks parsed -> {len(courses)} unique courses")
    return courses


def build_programs(refresh: bool = False) -> list[dict]:
    urls = fetch.program_page_urls(refresh=refresh)
    print(f"\n{len(urls)} candidate program pages in sitemap")

    programs: list[dict] = []
    skipped = 0
    for index, url in enumerate(urls, start=1):
        page = fetch.fetch(url, refresh=refresh)
        parsed = parse_programs.parse_page(page, source_url=url)
        if parsed is None:
            skipped += 1
            continue
        programs.append(parsed)
        groups = len(parsed["requirement_groups"])
        print(f"  [{index:>3}/{len(urls)}] {groups:>3} groups  {parsed['program_name'][:52]}")

    print(f"\n{len(programs)} pages with requirements ({skipped} without)")
    return programs


def build_graph(courses: list[dict]) -> dict:
    """Edge list over course codes. An edge (a -> b) means a is a prerequisite
    of b. Alternatives ("X or Y") produce an edge from each alternative, so the
    edge list alone is a *relaxed* graph; consult the tree on each course when
    you need the real boolean requirement."""
    known = {c["course_id"] for c in courses}
    edges: list[dict] = []
    dangling: list[dict] = []

    for course in courses:
        for prereq_code in course["prerequisites"]["courses"]:
            edge = {"from": prereq_code, "to": course["course_id"]}
            if prereq_code in known:
                edges.append(edge)
            else:
                dangling.append(edge)

    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])

    return {
        "edges": edges,
        "adjacency": {k: sorted(v) for k, v in sorted(adjacency.items())},
        "dangling_edges": dangling,
    }


def build_offerings(refresh: bool = False, semesters: list[str] | None = None) -> list[dict]:
    """One search request per semester with DEPT=All.

    DEPT=All returns the whole semester in a single ~9 MB response, so this is
    5 requests rather than 5 x 61 per-department ones.
    """
    semesters = semesters or fetch.SEMESTERS
    print(f"\nSchedule of Classes: {len(semesters)} semesters")

    offerings: list[dict] = []
    for semester in semesters:
        page = fetch.post(
            fetch.SOC_SEARCH,
            {
                "SEMESTER": semester,
                "MINI": "NO",
                "GRAD_UNDER": "All",
                "PRG_LOCATION": "All",
                "DEPT": "All",
            },
            refresh=refresh,
        )
        rows = parse_soc.parse_search(page, semester)
        offerings.extend(rows)
        print(f"  {semester}: {len(rows):>5} offering rows")

    print(f"\n{len(offerings)} offering rows across {len(semesters)} semesters")
    return offerings


def build_details(offerings: list[dict], refresh: bool = False,
                  semesters: list[str] | None = None) -> list[dict]:
    """Full section detail, one request per (course, semester). Opt-in: this is
    ~3,300 requests per semester at 1 req/sec."""
    wanted = set(semesters or [])
    targets = sorted({
        (o["course_id"], o["semester"]) for o in offerings
        if not wanted or o["semester"] in wanted
    })
    print(f"\nFetching details for {len(targets)} (course, semester) pairs")

    details: list[dict] = []
    for index, (course_id, semester) in enumerate(targets, start=1):
        number = course_id.replace("-", "")
        page = fetch.fetch(
            f"{fetch.SOC_DETAILS}?COURSE={number}&SEMESTER={semester}", refresh=refresh
        )
        details.append(parse_soc.parse_details(page, course_id, semester))
        if index % 100 == 0 or index == len(targets):
            print(f"  [{index}/{len(targets)}]")
    return details


def build_offerings_report(offerings: list[dict], courses: list[dict]) -> dict:
    known = {c["course_id"] for c in courses}
    by_semester: dict[str, int] = {}
    for row in offerings:
        by_semester[row["semester"]] = by_semester.get(row["semester"], 0) + 1

    referenced = {o["course_id"] for o in offerings}
    return {
        "offering_rows": len(offerings),
        "rows_by_semester": by_semester,
        "distinct_courses": len(referenced),
        "courses_in_catalog": len(referenced & known),
        "courses_not_in_catalog": len(referenced - known),
        "catalog_courses_never_offered": len(known - referenced),
        "rows_with_tba_time": sum(1 for o in offerings if o["tba"]),
        "rows_mini": sum(1 for o in offerings if o["is_mini"]),
        "distinct_locations": sorted({o["location"] for o in offerings if o["location"]}),
    }


def build_program_report(programs: list[dict], flat: list[dict], courses: list[dict]) -> dict:
    known = {c["course_id"] for c in courses}
    groups = [g for p in programs for g in p["requirement_groups"]]
    sample = [g for g in groups if g["is_sample_sequence"]]

    referenced = {row["course_id"] for row in flat}
    unknown = sorted(referenced - known)

    return {
        "programs": len(programs),
        "requirement_groups": len(groups),
        "sample_sequence_groups_excluded": len(sample),
        "requirement_rows": len(flat),
        "distinct_courses_required": len(referenced),
        "required_courses_not_in_catalog": len(unknown),
        "required_courses_not_in_catalog_sample": unknown[:25],
        "groups_without_caption": sum(1 for g in groups if not g["caption"]),
        "prose_rows": sum(
            1 for g in groups for r in g["rows"] if r["kind"] == "comment"
        ),
    }


def build_report(courses: list[dict], graph: dict) -> dict:
    with_prereqs = [c for c in courses if c["prerequisites"]["courses"]]
    review = [
        {
            "course_id": c["course_id"],
            "name": c["name"],
            "raw": c["prerequisites"]["raw"],
            "reason": c["prerequisites"]["review_reason"],
        }
        for c in courses
        if c["prerequisites"]["needs_review"]
    ]
    return {
        "courses": len(courses),
        "courses_with_prerequisites": len(with_prereqs),
        "courses_missing_units": sum(1 for c in courses if c["units"] is None),
        "courses_missing_description": sum(1 for c in courses if not c["description"]),
        "prereq_edges": len(graph["edges"]),
        "dangling_edges": len(graph["dangling_edges"]),
        "needs_review_count": len(review),
        "needs_review": review,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-fetch, ignoring cache")
    parser.add_argument(
        "--semesters", nargs="+", metavar="SEM",
        help=f"limit SOC to these semesters (default: {' '.join(fetch.SEMESTERS)})",
    )
    parser.add_argument(
        "--details", action="store_true",
        help="also fetch SOC per-course detail pages (~3,300 requests/semester)",
    )
    args = parser.parse_args()

    courses = build_courses(refresh=args.refresh)
    graph = build_graph(courses)
    report = build_report(courses, graph)

    programs = build_programs(refresh=args.refresh)
    requirements = parse_programs.flatten(programs)
    program_report = build_program_report(programs, requirements, courses)

    offerings = build_offerings(refresh=args.refresh, semesters=args.semesters)
    offerings_report = build_offerings_report(offerings, courses)

    print("\nGeneral education requirements")
    geneds = parse_geneds.build(programs, refresh=args.refresh)
    known = {c["course_id"] for c in courses}
    gened_report = {
        "categories": len(geneds),
        "schools": sorted({g["school"] for g in geneds}),
        "categories_with_courses": sum(1 for g in geneds if g["courses_available"]),
        "categories_without_courses": sum(1 for g in geneds if not g["courses_available"]),
        "total_course_entries": sum(g["course_count"] for g in geneds),
        "distinct_courses": len({c["course_id"] for g in geneds for c in g["courses"]}),
        "courses_not_in_catalog": len(
            {c["course_id"] for g in geneds for c in g["courses"]} - known
        ),
        "by_school": {
            school: {
                "categories": sum(1 for g in geneds if g["school"] == school),
                "courses": sum(g["course_count"] for g in geneds if g["school"] == school),
            }
            for school in sorted({g["school"] for g in geneds})
        },
    }
    for school, stats in gened_report["by_school"].items():
        print(f"  {school:9} {stats['categories']:>2} categories  {stats['courses']:>4} courses")

    outputs = [
        ("courses.json", courses),
        ("prereq_graph.json", graph),
        ("report.json", report),
        ("programs.json", programs),
        ("requirements.json", requirements),
        ("program_report.json", program_report),
        ("offerings.json", offerings),
        ("offerings_report.json", offerings_report),
        ("geneds.json", geneds),
        ("gened_report.json", gened_report),
    ]

    if args.details:
        details = build_details(offerings, refresh=args.refresh, semesters=args.semesters)
        outputs.append(("course_details.json", details))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs:
        path = DATA_DIR / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {path}")

    print(
        f"\ncourses:  {report['courses']} | "
        f"{report['courses_with_prerequisites']} with prerequisites | "
        f"{report['prereq_edges']} edges | "
        f"{report['dangling_edges']} dangling | "
        f"{report['needs_review_count']} need review"
    )
    print(
        f"programs: {program_report['programs']} | "
        f"{program_report['requirement_groups']} groups "
        f"({program_report['sample_sequence_groups_excluded']} sample-sequence) | "
        f"{program_report['requirement_rows']} requirement rows | "
        f"{program_report['distinct_courses_required']} distinct courses"
    )
    print(
        f"schedule: {offerings_report['offering_rows']} offering rows | "
        f"{offerings_report['distinct_courses']} distinct courses | "
        f"{offerings_report['courses_in_catalog']} in catalog | "
        f"{offerings_report['courses_not_in_catalog']} not in catalog"
    )


if __name__ == "__main__":
    main()
