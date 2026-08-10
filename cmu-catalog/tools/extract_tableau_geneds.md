# Refreshing the Tableau-backed gen-ed lists

CIT and Dietrich both publish their approved general-education courses through
embedded **Tableau Public** workbooks. Neither is reachable over plain HTTP:

- `.../CourseSearch.csv` → 404
- `/vizql/w/<wb>/v/<view>/viewData/...` → 404
- `public.tableau.com/profile/api/...` → empty
- workbook metadata reports `allowDataAccess: false`

The workbooks *do* render a real table into the DOM as `[role="gridcell"]`
elements, so the data is extracted with a browser and committed as a snapshot.

| school | workbook | view |
|---|---|---|
| CIT | `COECourseSearch` | `CourseSearch` |
| Dietrich | `GeneralEducationPublicSearchTool` | `GenEdDashboard` |

## CIT — works end to end

The whole result set renders at once, so one pass per semester is enough.

```
https://public.tableau.com/views/COECourseSearch/CourseSearch?:embed=y&:showVizHome=no&:tabs=no&:toolbar=no&Semester%20Id=F26
```

Semester is settable by URL (`F25`, `S26`, `F26` are populated; passing several
comma-separated values yields zero rows, so iterate one at a time). Then run the
extraction snippet below and append the rows to
`data/cit_gened_snapshot.json`.

## Dietrich — partially blocked

The landing view (`GenEdLanding`) is a canvas campus picker; clicking a campus
navigates to `GenEdDashboard`. Columns are Course, Long Title, Semester,
Category, College, Department, and the categories are the real learning areas
("Scientific Inquiry", …).

**The blocker:** its table is virtualised inside a `.tab-tvYLabel` element whose
`scrollTop` is not settable (`overflow: hidden`, Tableau drives its own
scrollbar). Wheel events, keyboard paging, `:size=`, and a 5000px viewport all
leave the row count unchanged, so only the first ~35 rows are reachable. URL
filters do work:

```
...&Category=Scientific%20Inquiry&Semester=F26
```

but even a single category in a single semester overflows (771px of content in
a 564px window ≈ 17 of ~23 rows), so filtering alone does not close the gap
without also slicing by Department.

**Data export is disabled by the author.** The workbook metadata reports
`allowDataAccess: false`, and the toolbar's Download menu offers only **Image,
PDF and PowerPoint** — there is no Data or Crosstab option. (An earlier version
of this file suggested Crosstab → CSV; that was wrong, and checking the menu is
what disproved it.)

Replaying Tableau's own handshake gets partway and then stops:
`POST /vizql/w/<wb>/v/<view>/startSession/sessions/<hex>-0:0/viewing` returns
200 with a `newSessionId`, but `POST .../bootstrapSession/sessions/<newSessionId>`
answers **410 Gone**. The bootstrap payload from a real page load *does* contain
the whole data dictionary (~2 MB), so the data is all there — it just is not
reachable without a live browser session.

Remaining options, cheapest first:

1. **PDF export.** Tableau's PDF renderer often lays out the entire crosstab
   rather than the on-screen window. One click, and if it does, the full table
   comes out as text. Worth trying before anything else.
2. **Ask the owner.** The workbook is published by `george.david.cann` on
   Tableau Public; the Dietrich Gen Ed office maintains the underlying list.
   One email gets the source data in a usable form.
3. **Grind the filters.** `&Department=…&Semester=…` slices do fit the window,
   but that is roughly 40 departments × 3 semesters of navigation.

Until that CSV exists, `parse_geneds.dietrich()` deliberately emits categories,
unit rules and timelines (which are complete, and come from the plain-HTML
curriculum page) with an empty `courses` list, rather than a partial list that
would look complete and silently under-report.

## Extraction snippet

Run in the browser console on a rendered viz. `xs` is the set of column
x-offsets — read them off the first run and adjust per workbook.

```js
(() => {
  const c = [...document.querySelectorAll('[role="gridcell"]')];
  const xs = [...new Set(c.map(e => Math.round(e.getBoundingClientRect().x)))].sort((a, b) => a - b);
  const by = {};
  c.forEach(e => {
    const r = e.getBoundingClientRect();
    const y = Math.round(r.y / 3) * 3;
    const ci = xs.findIndex(x => Math.abs(x - Math.round(r.x)) < 40);
    if (ci < 0) return;
    by[y] = by[y] || {};
    by[y][ci] = ((by[y][ci] || '') + ' ' + (e.innerText || '').trim()).trim();
  });
  return JSON.stringify(Object.values(by).filter(r => /^\d{5}$/.test(String(r[0] || ''))));
})()
```

Rows come back column-major-safe because cells are grouped by their rendered
y position; a course whose title wraps produces one row, not two. Blank leading
cells mean a continuation row (the same course in another semester), so carry
the previous course number forward when flattening.
