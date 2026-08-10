# cmu-catalog

Data foundation for the degree-path tool: CMU's course catalog, prerequisite
graph, degree requirements, and course offerings. Built entirely from public,
unauthenticated sources. No credentials, no FCE data.

- **Step 1** — courses + prerequisite graph (CourseLeaf `/courses/` pages)
- **Step 2** — degree requirements per program (CourseLeaf program pages)
- **Step 3** — offerings per semester (Schedule of Classes)
- **Step 4** — general education requirements per school (four separate sources)

## Run

```bash
python build.py
```

Stdlib only, no dependencies. Cold run ~2.5 min (111 catalog pages at 1 req/sec
plus 5 SOC requests); instant afterwards off `cache/`.

```bash
python build.py --refresh                 # ignore cache
python build.py --semesters F26 S26       # limit SOC semesters
python build.py --details                 # + per-course SOC detail (slow, see below)
```

On Windows set `PYTHONIOENCODING=utf-8` if your console is cp1252.

## What it produced

| courses | | programs | | schedule | |
|---|---|---|---|---|---|
| unique courses | 4,263 | pages with requirements | 50 | offering rows | 11,020 |
| with prerequisites | 1,508 | requirement groups | 1,661 | distinct courses | 5,853 |
| prerequisite edges | 4,317 | sample-sequence (excluded) | 55 | in catalog | 3,066 |
| edges outside catalog | 498 | requirement rows | 10,556 | not in catalog | 2,787 |
| flagged for review | 66 | rows joining to catalog | **98.6%** | departments | 56 |

Offerings by semester: F26 3,327 · S26 3,283 · F25 3,304 · M26 539 · M25 567.

## Output

`data/courses.json`

```json
{
  "course_id": "15-451", "name": "Algorithm Design and Analysis",
  "units": 12.0, "semesters": ["Fall", "Spring"], "description": "...",
  "prerequisites": {
    "raw": "15-210 Min. grade C and 21-241 Min. grade C and ( 15-251 ... )",
    "tree": {"and": [{"course": "15-210"}, {"course": "21-241"},
                     {"or": [{"course": "15-251"}, {"course": "21-228"}]}]},
    "courses": ["15-210", "21-241", "15-251", "21-228"],
    "needs_review": false, "review_reason": null
  },
  "corequisites": {"...same shape..."},
  "source_urls": ["http://coursecatalog.web.cmu.edu/..."]
}
```

`data/programs.json` — requirement groups nested under each program, with
`credential`, `section`, `caption`, `total_units`, `is_sample_sequence`, and
rows of `kind: "course"` (with `alternatives`) or `kind: "comment"`.

`data/requirements.json` — flattened `(program, credential, section, group,
course)` rows for joining. Sample-sequence groups excluded.

`data/offerings.json`

```json
{
  "semester": "F26", "course_id": "15-112", "department": "COMPUTER SCIENCE",
  "session": null, "name": "Fundamentals of Programming and Computer Science",
  "units": 12.0, "units_min": 12.0, "units_max": 12.0, "units_raw": "12",
  "section": "Lec 1", "is_mini": false,
  "days": ["Tuesday", "Thursday"], "days_raw": "TR",
  "begin": "09:30AM", "end": "10:50AM", "tba": false,
  "location": "Pittsburgh, Pennsylvania", "delivery_mode": "In-person Expectation"
}
```

`data/prereq_graph.json`, `data/report.json`, `data/program_report.json`,
`data/offerings_report.json` — graph and per-step counts + review queues.

`data/course_details.json` — only with `--details`.

### Reading the data

**Requirement quantifiers live in text, not structure.** `caption`, `section`
and `credential` carry "all of the following", "minimum 5 courses", "select
two". Kept verbatim rather than parsed into intent, because being silently
wrong is worse. 886 of 1,661 groups have no caption; there the meaning is in
`section`.

**`alternatives` is the or-list** (610 rows). **`kind: "comment"` rows are real
requirements** (1,066), often carrying units — the constraint is genuinely
prose and can't reduce to a course list.

**The prereq edge list is relaxed.** "15-251 or 21-228" becomes two edges, so it
over-connects. Use it for reachability; use `prerequisites.tree` for the actual
requirement.

**Offerings are one row per course, not per section.** The SOC search endpoint
returns only the primary meeting — 3,382 rows for 3,327 courses in F26. Full
section lists need `--details`.

**Units are often variable.** `units` is set only for fixed-credit courses
(8,846 rows). Ranges and sets (`3-18`, `0,36`) populate `units_min`/`units_max`
(1,526 rows); `VAR` leaves all three null (648 rows). `units_raw` is always
preserved.

## General education (`data/geneds.json`)

19 categories across the four schools. Each school publishes these completely
differently, and **only two publish an enumerated course list at all**.

| school | source | categories | courses |
|---|---|---|---|
| SCS | JSON endpoints on cs.cmu.edu | 8 | 329 |
| MCS | CourseLeaf catalog tables | 6 | 151 |
| CIT | prose page + Tableau DOM snapshot | 6 | 144 |
| Dietrich | curriculum tables + Tableau PDF snapshot | 14 | 277 |

**554 distinct approved courses**, of which 38 aren't in the catalog.

```json
{
  "school": "SCS", "section": "Humanities and Arts",
  "category": "Category 1: Cognition, Choice and Behavior",
  "slug": "scs-gen-ed-reqs-cat1", "list_type": "approved",
  "courses": [{"course_id": "70-311", "title": "Organizational Behavior",
               "units": 9.0, "units_raw": "9"}],
  "course_count": 27, "courses_available": true,
  "source_url": "https://www.cs.cmu.edu/rs-datasources/data-source/scs-gen-ed-reqs-cat1"
}
```

**Check `list_type` before joining.** Not every list is a list of courses that
*satisfy* a requirement:

| list_type | meaning | distinct courses |
|---|---|---|
| `approved` | satisfies the requirement | 292 |
| `added` | recently added to a list | 43 |
| `excluded` | explicitly does **not** count (SCS "Deletions", "Non-Qualifying Courses") | 84 |

Treating all 329 SCS entries as approved would silently invert the meaning of
84 of them. Four courses appear as approved under one school and excluded under
another — that's legitimate, since approval is per-school, not global.

**Check `courses_available` too.** CIT and Dietrich records carry categories,
descriptions and unit rules but an empty `courses` array, with
`unavailable_reason` explaining why. That distinguishes "no courses qualify"
from "the list isn't published", which a bare empty array would not.

### CIT and Dietrich are Tableau-backed

Both publish their approved-course lists through embedded **Tableau Public**
workbooks with no HTTP data endpoint (`.csv` and `/vizql/.../viewData` both 404;
the workbook metadata reports `allowDataAccess: false`). The workbooks do render
real tables into the DOM, so the data is extracted with a browser.

**CIT** — done. Categories and unit rules come from the prose page
("9 units from the PPC list; or a 9-12 unit course in a modern language"); the
courses come from the `COECourseSearch` workbook, extracted for F25/S26/F26 and
committed to `data/cit_gened_snapshot.json` (144 courses, with `semesters` on
each). The workbook exposes two categories the prose page doesn't name:
`General Education Elective`, and `Free Elective Only` — the latter is an
**exclusion**, tagged `list_type: "excluded"`.

**Dietrich** — done, via PDF. The curriculum page gives the requirement
structure as plain HTML tables: 14 categories across Foundations (54 units),
Disciplinary Perspectives (42), Special Seminars (18) and Experiential Learning
(1) — 115 units total — each with description, units and timeline.

The course lists sit in a second workbook
(`GeneralEducationPublicSearchTool/GenEdDashboard`), and that one resists
scraping in every direction: `allowDataAccess: false`, the Download menu offers
only Image/PDF/PowerPoint, the table is virtualised in a fixed-height dashboard
zone whose `scrollTop` is pinned at 0 (wheel events, keyboard paging, `:size=`
and a 5000px viewport all leave the row count unchanged), and replaying
Tableau's session handshake gets a 200 from `startSession` but a **410** from
`bootstrapSession`. Only ~35 of ~450 rows are reachable through the DOM.

**PDF export is the way through.** Tableau's PDF renderer lays out the entire
crosstab rather than the on-screen window — 30 pages, 724 course-semester rows,
271 distinct courses across all 13 populated categories.
[tools/convert_dietrich_pdf.py](tools/convert_dietrich_pdf.py) turns it into
`data/dietrich_gened_snapshot.json`:

```bash
python tools/convert_dietrich_pdf.py "Dietrich GenEd Course List.pdf"
```

Only `Experiential Learning Activity` has no course list, which is correct — it
is an activity requirement, not a course requirement.

See [tools/extract_tableau_geneds.md](tools/extract_tableau_geneds.md) for the
refresh procedure for both workbooks.

## The three course sources, and what each is good for

| | catalog `/courses/` | program pages | Schedule of Classes |
|---|---|---|---|
| descriptions | yes | — | with `--details` |
| prerequisites | yes (prose) | — | yes, **parenthesized** |
| degree requirements | — | yes | — |
| which semesters a course actually runs | approximate | — | yes |
| sections, times, locations | — | — | yes |
| instructors | no | no | **no** |
| graduate coverage | poor | poor | good |

**Nothing here has instructors.** That was an open question from planning and
the answer is definitive: the SOC search table has ten columns and none is an
instructor, and `courseDetails` contains no instructor field either. The FCE
dataset does carry them — one more reason the uro-fce request matters.

**SOC prerequisites are better structured than the catalog's.** SOC returns
`(15-210) and (21-241) and (15-251 or 21-228)` — explicitly parenthesized, no
prose, no grade qualifiers. The catalog's prose version of the same course is
where all the ambiguity lives. With `--details` you can cross-validate the
catalog trees against SOC's, and probably resolve most of the 8 ambiguous cases.

**SOC covers graduate courses the catalog omits.** 2,787 courses appear in SOC
but not the catalog — mostly Heinz (`90`/`94`/`95`), Tepper graduate (`45`), and
the graduate half of departments whose catalog page is undergraduate-only
(Music `57`, Drama `54`, Design `51`, S3D `17`). Conversely 1,197 catalog
courses were not offered in any of the five available semesters.

## `--details`, and why it's opt-in

`courseDetails` is one request per (course, semester) — roughly 3,300 per
semester, so ~55 minutes per semester at 1 req/sec. It adds full section
hierarchies (15-112 has 23 sections under 3 lectures), enrollment restrictions,
cross-listings, and the clean prerequisite strings above.

Scope it: `python build.py --details --semesters F26`.

## How it stays within bounds

- Catalog crawl frontier is the catalog's own `sitemap.xml`, which `robots.txt`
  points at. In-page links are never followed, so excluded paths can't be hit.
- `fetch.py` asserts catalog URLs against the `robots.txt` disallow list and
  raises `DisallowedPath` rather than fetching. Course links point at
  `/search/?P=...`, which is disallowed — the anchor *text* is read, never the href.
- The disallow list is scoped to the catalog host; `enr-apps.as.cmu.edu` serves
  no robots.txt (404) and its `/SOC/` paths would otherwise collide with
  catalog rules like `/search/`.
- 1 request/second, real User-Agent with a contact address (`CATALOG_CONTACT`
  to override), everything cached to disk so re-runs cost CMU nothing.
- SOC uses `DEPT=All`: one request per semester instead of 61.

## Known limitations

**The catalog is undergraduate.** Absent prefixes: `14` INI, `45`/`46`/`47`
Tepper graduate, `90`/`91`/`93`/`94`/`95` Heinz. Of the 83 courses required by a
program but missing from the catalog, most are `98` StuCo (never catalogued),
`53` ETC, `49` III, plus the above. SOC partially fills this.

**Some prerequisite groupings are ambiguous.** 66 flagged in `report.json`: 52
prose-only, 8 mixing and/or without parentheses, 6 prose alongside codes. Only
the 8 may be actively wrong — `"18-345 and senior or graduate standing"` parses
by standard precedence as `(18-345 and senior) or graduate standing` but reads
to a human as `18-345 and (senior or graduate standing)`. Flagged, not guessed.
SOC's parenthesized strings can likely resolve these.

**SOC's archive is shallow** — only five semesters are offered by the form
(F25 through F26). There is no deeper history available here.

**`total_units` is usually absent** — only 185 of 1,661 groups publish a
summary row.

## Bugs found and fixed

All were silent. Recorded so they don't get reintroduced.

1. **Grade qualifiers truncated prerequisite expressions.** `Min. grade C` was
   stripped to end-of-string, so `"15-112 Min. grade C or 02-120 Min. grade C"`
   lost `02-120`. Dropped at least one course from **244 of 1,563** strings and
   cost 890 graph edges. Now removed inline.
2. **Units-only offering lines misparsed** (`09-323` opens with `"12 units"`,
   no semester).
3. **Zero-width characters** in catalog headings — invisible in data, hard crash
   on a cp1252 console.
4. **SOC returns 56 tables, not one.** With `DEPT=All` each department gets its
   own table, all sharing `id="search-results-table"`. Parsing only the first
   yielded 0 rows. The department name lives in a preceding `<h4>` and is now
   captured.
5. **SOC never closes `<tbody>`.** The table ends `</tr></table>`, so a
   `<tbody>...</tbody>` regex matched nothing. Row extraction now anchors on
   `</thead>`.
6. **Summer semesters have an extra `Session` column.** Positional column
   mapping shifted every field after it — section became `"12"`, the end time
   landed in `location`. The detail page had the same problem via a leading
   blank column. **Both now derive column order from each table's own header
   row**, which is the general fix.

Bugs 4–6 are why the SOC parser is header-driven rather than positional: this
source varies its shape by semester and by endpoint.

## Next

The three public sources are exhausted. Remaining work is joining them into a
degree-path model, and the FCE dataset if `uro-fce@andrew.cmu.edu` grants it.
Note the three ID formats: catalog `15-122`, SOC `15112`, FCE `Dept`+`Num` —
all normalised to `NN-NNN` on ingest.
