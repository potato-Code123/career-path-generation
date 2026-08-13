# Phase 2 notes — career taxonomy ingestion and free-text resolution

Everything below is measured from the pinned sources in `data/raw/`, not recalled.
Regenerate the numbers with the commands in each section.

---

## 1. Pinned release labels and hashes

From `data/raw/SOURCE_MANIFEST.yaml`, retrieved 2026-08-08 UTC.

| key | file | release label | sha256 |
|---|---|---|---|
| `onet_database` | `db_30_3_text.zip` | O\*NET 30.3 Database (May 2026 Release) | `7758ec966fd91895b3d290b83c9f1f1d46730d37fdda4faac67104d1c0d2a780` |
| `onet_all_occupations` | `All_Occupations.csv` | O\*NET OnLine All Occupations export (site release 30.3) | `699a5a2a1c1a855e5a9440db200c5dacd9a876aef0f599a661b533f9c5272730` |
| `bls_soc_structure` | `soc_structure_2018.xlsx` | BLS SOC 2018 structure | `ade08af40923266f3a854842e888ca3e93c15b26a147c20a2b12a61f4c4f4077` |
| `cip_soc_crosswalk` | `CIP2020_SOC2018_Crosswalk.xlsx` | NCES CIP 2020 to SOC 2018 Crosswalk | `ba3d59a191b9d977a5c457a66b9348c4f2f7963aafacf72c0b80113b46bf0ab8` |

The archive's own `Read Me.txt` opens with `O*NET 30.3 Database`, and
`validate_source_release.py` asserts that string on every build — a wrong bundle
fails before anything is written.

**Two sources need explaining.**

`All_Occupations.csv` is a fourth source the phase brief does not name. It is
required by Step 3: the exclusion rule is "any code whose `Data-level` is blank
in O\*NET", and the database bundle does not carry that column at all. The
column is published only in the O\*NET OnLine *All Occupations* export, so that
export is pinned alongside the database. Its code set is asserted identical to
`Occupation Data.txt` (1,016 codes, exact match), which is what makes it safe to
treat the two as one release. It flags **93** residual codes overall, five of
them ECE-relevant: `17-2199.00`, `15-1299.00`, `15-2099.00`, `17-3019.00`,
`17-3029.00`.

`soc_structure_2018.xlsx` was acquired manually. `bls.gov` returns **HTTP 403** to
automated clients from this host — not a 404, and not specific to the file: every
path including the directory index returns the same "Access Denied / bot
activity" page. That is a transport refusal, not a missing release, so
`fetch_source_files.py` registers it as a manual source: it keeps the real URL
for provenance, pins the hash once the file is present, and otherwise reports
exact instructions rather than silently substituting a different SOC edition.

---

## 2. Hierarchy conflicts surfaced by the BLS validation

`build_soc_hierarchy.py` writes **2,463 nodes** (1,016 `onet_detail`, 867
`detailed`, 459 `broad`, 98 `minor`, 23 `major`). The 1,447 non-`onet_detail`
nodes match the BLS structure file's row count exactly, which is asserted in
`tests/test_soc_hierarchy.py`.

String decomposition disagreed with the BLS file on **14 edges**, all logged to
`dev/reports/soc_hierarchy_conflicts.csv`. Every one is the same underlying
irregularity: a minor or broad group whose code does not follow the "zeros to the
right" pattern decomposition assumes, so decomposition proposes a group code that
**does not exist**.

| proposed by decomposition | does not exist | BLS placement | affected nodes |
|---|---|---|---|
| `15-1000` | ✓ | `15-1200` Computer Occupations | `15-1210`, `15-1220`, `15-1230`, `15-1240`, `15-1250`, `15-1290` (6 broad groups) |
| `29-1220` | ✓ | `29-1000` Healthcare Diagnosing or Treating Practitioners | `29-1221`–`29-1224`, `29-1229` (5 detailed) |
| `31-1000` | ✓ | `31-1100` Home Health and Personal Care Aides… | `31-1120`, `31-1130` (2 broad) |
| `51-5000` | ✓ | `51-5100` Printing Workers | `51-5110` (1 broad) |

The first row is the one that matters for this phase. `15-1200` (Computer
Occupations) is a minor group with a non-zero fourth digit, so naive
decomposition of `15-1221.00` (Computer and Information Research Scientists —
an ECE leaf) proposes the non-existent `15-1000` and the ladder breaks one rung
below where the backoff needs it. With the BLS file as authority the ladder is
`15-1221.00 → 15-1221 → 15-1220 → 15-1200 → 15-0000`, which is what
`apply_career_backoff` walks. Had decomposition been trusted, every `15-12xx`
resolution would have failed to find its minor group and backed off straight to
the major group — over-generalising six of the fourteen leaf-set entries.

Zero conflicts were of the "both codes exist but disagree" kind, and zero nodes
were ambiguous between multiple containing groups. The remaining classes the
brief warned about were present but did **not** produce conflicts, because the
prefix-matching rule handles them without special-casing:

- *A broad group and its single detailed occupation sharing a title.* `11-1010`
  and `11-1011` are both "Chief Executives". They stay distinct nodes with a real
  edge between them (asserted in `test_broad_and_detailed_may_share_a_title_without_collapsing`).
- *Residual "All Other" codes.* `17-2199.00` sits under `17-2190 → 17-2000 →
  17-0000` normally. They are excluded from the leaf set but kept in the ladder,
  because they are legitimate backoff waypoints.

---

## 3. Fuzzy threshold: chosen value and evidence

**Chosen: `threshold: 0.600`**, recorded in `config/fuzzy_match_settings.yaml`
and read from there by `match_title_fuzzy.py`. Evidence:
`dev/reports/fuzzy_threshold_sweep.csv`, produced by
`dev/tune_fuzzy_match_threshold.py` over 27 probes in three groups (15 that
should hit, 6 nonsense strings that should miss, 6 real occupations outside ECE
that must not leak into the ECE namespace).

| threshold | should-hit recall | false-match on nonsense | ECE leak | mean candidates |
|---|---|---|---|---|
| 0.500 | 1.000 | 0.333 | 0.167 | 7.60 |
| 0.550 | 1.000 | 0.167 | 0.167 | 7.00 |
| **0.600** | **1.000** | **0.000** | **0.167** | **6.13** |
| 0.625 | 0.933 | 0.000 | 0.167 | 5.00 |
| 0.675 | 0.867 | 0.000 | 0.167 | 3.69 |
| 0.750 | 0.800 | 0.000 | 0.000 | 3.08 |

0.600 is the knee, and it is a genuine knee rather than a judgement call: it is
simultaneously the **highest** threshold at which should-hit recall is still
1.000 and the **lowest** at which the nonsense false-match rate reaches 0.000.
Every step above it trades recall away for nothing — the false-match rate is
already zero. The binding probe underneath is `flurbish wrangler` at 0.589; the
binding probe above is `chip designer` at 0.619.

The residual 0.167 ECE-leak rate at 0.600 is one probe, `prompt engineer`, whose
best match is `prompter` (27-2099.00) at 0.537 — below the threshold, so it does
not actually leak at this setting. It only clears at 0.475 and below.

**A second value is recorded: `llm_candidate_floor: 0.350`.** This is a
deviation, explained in §6.

---

## 4. Which of the 12 exit cases required the LLM stage

**None of the twelve produce their expected outcome from stage 4.** Ten resolve
in stages 1–3; the other two reach stage 4 only to be told there is no match.

| input | stage reached | codes |
|---|---|---|
| `design verification engineer` | `exact` | 17-2061.00, 17-2071.00 |
| `Design Verification Engineer` | `exact` | identical to the above |
| `ML engineer` | `expanded` | 15-1221.00, 15-1299.08, 15-2051.00 |
| `MLE` | `expanded` | same set |
| `RTL designer` | `fuzzy` | includes 17-2061.00 (backoff narrows to exactly it) |
| `FPGA engineer` | `exact` | 17-2061.00, 17-2072.00 |
| `embedded engineer` | `exact` | 15-1252.00 |
| `chip designer` | `fuzzy` | 25-1032.00 |
| `quant` | **reaches stage 4**, 5 candidates | → `unmapped` |
| `prompt engineer` | **reaches stage 4**, 8 candidates | → `unmapped` |
| `power systems engineer` | `exact` | 17-2071.00, 17-2199.11 |
| `asdfqwer` | `unmapped` | no candidate clears even the relaxed floor; no call made |

So **2 of 12** invoke the model at all, and in both the correct answer is
`UNMAPPED` — the caller elicits. `asdfqwer` deliberately does not spend a call:
its best score is 0.239, under the 0.350 relaxed floor.

This is the intended shape. The model is the narrowest stage in the cascade, not
the workhorse: it is reached only when 49,708 index keys, 33 abbreviation
expansions and a character n-gram search have all failed, and it can only choose
from what stage 3 already found.

`tests/test_resolution_without_network.py` proves the ten stage-1–3 cases hold
with the client monkeypatched to raise, and additionally asserts that the default
client is never even *constructed* for them.

Practical note: `chip designer` resolving to `25-1032.00` (Engineering Teachers,
Postsecondary) at 0.619 is the weakest result in the table. It satisfies the
brief's "non-empty" expectation and `apply_backoff` recovers sensibly via
`cip_prior`, but it is a fuzzy match on the word *designer* rather than a real
one. With a live model, stage 4 would very likely reject it. See §7.

---

## 5. `ece_career_leaf_set.yaml` entries flagged for human review

14 entries; 11 justified, **3 flagged with a deliberately blank `justification`**.

| code | title | why it is uncertain |
|---|---|---|
| `17-2199.07` | Photonics Engineers | Sits under the `17-2199` "Engineers, All Other" stem. Whether photonics is an ECE destination or a separate optics/applied-physics track is a departmental question, and the answer changes whether the optics sequence should show up as high-lift for ECE students. |
| `17-2199.06` | Microsystems Engineers | Same stem. MEMS work overlaps heavily with mechanical engineering; including it may pool two different course populations and flatten the lift signal for both. |
| `15-1211.00` | Computer Systems Analysts | Reads closer to an information-systems destination than an ECE one. Included because "systems analyst" is a common self-description among CE graduates, but if the department does not consider it a target this leaf will absorb resolutions that belong on `15-1299.08`. |

All three are **included** in the file, so the pipeline runs; the blank
justification is the flag. Each is a live decision, not a formatting oversight.

Excluded on principle, per Step 3: `17-2199.00`, `15-1299.00`, `15-2099.00`,
`17-3019.00`, `17-3029.00` — every ECE-relevant code with a blank `Data-level`.
These remain reachable as backoff waypoints in the hierarchy but can never be a
resolution target. `tests/test_career_title_resolution.py` asserts both halves of
that.

---

## 6. Deviations from the phase specification

Three, each forced and each localised.

**a. `temperature 0` is not settable on the current models.** The brief specifies
temperature 0 for stage 4. That parameter no longer exists: `temperature`,
`top_p` and `top_k` were removed on Claude Opus 5 / Opus 4.8 / 4.7 and Sonnet 5,
and sending any of them returns HTTP 400. There is no replacement knob.

What temperature 0 was buying — reproducibility of the career namespace — is
delivered instead by the mechanism the brief itself specifies: the resolution
cache makes stage 4 run **once per novel title, ever**, and records the
`model_id` that produced each row. A cached resolution replays byte-identically
regardless of what the model would say on a re-ask. Within a single call, the
blast radius of any residual sampling variation is bounded to "one of the
candidates stage 3 already proposed, or UNMAPPED", enforced by an
`output_config.format` enum *and* a Python-side membership check. Determinism is
a property of the cache, not of the sampler.

**b. `cache/career_title_resolutions.csv` has a seventh column.** The brief lists
six (`input_raw, normalized, codes, stage, resolved_utc, model_id`);
`candidates_considered` is added. This is forced by another requirement of the
same brief: `tests/test_resolution_determinism.py` must show that resolving from
a warm cache file produces a **byte-identical `Resolution` tuple**, and
`candidates_considered` is a field of that tuple. Recomputing it on a cache hit
would mean re-running the fuzzy stage, defeating the cache; defaulting it to the
resolved codes would make warm-cache tuples differ from cold-cache ones and fail
the test. The two requirements are in direct conflict; the determinism one is
load-bearing for the whole phase, so the column was added rather than the
guarantee weakened.

**c. `llm_candidate_floor` is a second threshold in `fuzzy_match_settings.yaml`.**
The brief says stages fire in order with "first hit wins", *and* that stage 4
selects "from the stage-3 candidate set only". Taken together those are
contradictory: if stage 3 hitting ends the cascade, stage 4 only ever runs when
stage 3 found nothing, and there is no candidate set to choose from. The
resolution: stage 3 *hits* at `threshold` (0.600); when nothing clears it, the
candidate set handed to stage 4 is gathered at the lower `llm_candidate_floor`
(0.350). Both numbers are in config and both appear in the sweep report. If
nothing clears even the floor, the answer is `unmapped` with no call made.

**Two smaller notes.** `tests/test_source_layout_boundaries.py` is a test file the
layout does not list, added because the brief separately requires a test that no
`src/` module imports from `dev/`; it seemed better to name it honestly than to
bury a layout assertion in an unrelated module. And `anthropic` is pinned as an
optional `llm` extra rather than a core dependency, so that stages 1–3 and every
build script run on a machine with no SDK and no API key — which is what makes
the no-network test meaningful.

---

## 7. Things worth knowing before Phase 3 consumes this

- **`reported_title` contributes zero rows to the index.** All 9,095
  (normalized_title, code) pairs derivable from *Sample of Reported Titles* are
  already present from *Job Titles*. The column is still unioned and still in the
  schema — a future release could diverge — but it currently adds nothing, so do
  not read a `source_column` distribution as evidence about that file.
- **The index is 62,028 rows over 49,708 distinct keys, and 7,908 keys map to
  more than one code.** That one-to-many-ness is the data, not noise. Any
  downstream consumer that needs a single code per career must get it from
  `apply_backoff`, which reports `ambiguous` and refuses to pick, rather than by
  taking `codes[0]`.
- **`backoff_level` must be read from the `Resolution`, never re-derived.** It is
  recorded at the moment the decision is made. Re-deriving it later against a
  different leaf set or a re-built hierarchy would produce a different answer
  than the one the student was actually shown.
- **The CIP prior is expert judgement, not placement data.** `cip_soc_prior.parquet`
  comes from analysts reading code descriptions; nobody observed a graduate
  taking a job to build it. Explanation text for a `cip_prior` backoff must say
  "programs like yours are associated with these occupations", never "graduates
  of your program become these things".
- **`chip designer` is the known weak spot** (§4). If Phase 3 surfaces more
  inputs like it, the cheapest fix is an expansion entry, not a lower threshold —
  the sweep shows lowering the threshold buys false matches on nonsense before it
  buys anything real.

---

## Regenerating everything

```bash
PYTHONPATH=src python -m occupational_scrape.fetch_source_files
PYTHONPATH=src python -m occupational_scrape.validate_source_release
PYTHONPATH=src python -m occupational_scrape.build_soc_hierarchy
PYTHONPATH=src python -m occupational_scrape.build_occupation_titles_index
PYTHONPATH=src python -m occupational_scrape.build_cip_soc_prior
PYTHONPATH=src python dev/tune_fuzzy_match_threshold.py
PYTHONPATH=src python dev/inspect_ece_title_coverage.py
python -m pytest
```
