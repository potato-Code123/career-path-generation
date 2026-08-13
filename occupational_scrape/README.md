# occupational_scrape — Phase 2

Produces the **career label namespace** that the statistics layer conditions on,
and resolves a student's free-text career goal onto it.

The whole phase exists to make one property true: **the same input string must
resolve to the same career code on every run, forever.** Lift is computed as
`P(course | career) / P(course)` keyed on that code. A mapping that drifts
between runs does not raise an error and does not show up in a diff — it just
silently changes every statistic derived from it. Read
[`docs/phase2_notes.md`](docs/phase2_notes.md) for the measured results and the
places the specification had to be deviated from.

## Layout

```
config/     hand-editable inputs: leaf set, abbreviations, fuzzy settings
data/raw/   downloaded sources, never edited; SOURCE_MANIFEST.yaml pins hashes
data/processed/  parquet artifacts consumed by code, never by a human
cache/      append-only resolution cache — the reproducibility mechanism
src/        the pipeline
dev/        diagnostics; outputs in dev/reports/; never imported by src/ or tests/
tests/
docs/
```

The `src/` vs `dev/` split is enforced, not conventional: if a script's output is
read by a human rather than by code, it belongs in `dev/`, and
`tests/test_source_layout_boundaries.py` fails the build if anything under
`src/occupational_scrape/` imports from `dev`.

## Setup

Python 3.11+.

```bash
python -m venv .venv && .venv/bin/pip install -e .
```

Stage 4 needs the optional extra and an API key; **everything else runs without
either**, which is what makes the no-network test meaningful:

```bash
.venv/bin/pip install -e '.[llm]'
```

## Build

```bash
PYTHONPATH=src python -m occupational_scrape.fetch_source_files
PYTHONPATH=src python -m occupational_scrape.validate_source_release
PYTHONPATH=src python -m occupational_scrape.build_soc_hierarchy
PYTHONPATH=src python -m occupational_scrape.build_occupation_titles_index
PYTHONPATH=src python -m occupational_scrape.build_cip_soc_prior
```

`fetch_source_files` verifies the sha256 of every file already present and skips
the download. **A hash mismatch is a hard error, never a re-fetch.**

One source cannot be downloaded from this host: `bls.gov` returns HTTP 403 to
automated clients on every path. The fetcher reports exact manual instructions
and pins the hash once the file is in place — it will not substitute a different
SOC release. See `docs/phase2_notes.md` §1.

`validate_source_release` is not optional politeness. An earlier attempt at this
pipeline used O\*NET release 20.3 — the 2010 taxonomy — whose codes are 24%
absent from the 2019 one, so the join dropped a quarter of the data with no
error. Those assertions run on every build.

## Resolving a title

```python
from occupational_scrape.resolve_career_title import resolve_career_title
from occupational_scrape.apply_career_backoff import apply_backoff

resolution = resolve_career_title("RTL designer")
# Resolution(codes=(...8 codes...), stage='fuzzy', ...)

narrowed = apply_backoff(resolution)
# codes=('17-2061.00',), backoff_level='leaf'
```

Or from the shell:

```bash
PYTHONPATH=src python -m occupational_scrape.resolve_career_title "RTL designer" "ML engineer"
```

### The cascade

| stage | what it does |
|---|---|
| `exact` | normalized input matches a key in `title_index.parquet` |
| `expanded` | ECE abbreviations expanded, stage 1 retried on each variant |
| `fuzzy` | character n-gram TF-IDF, cosine ≥ threshold, top-k codes |
| `llm` | Claude picks one code from the stage-3 candidates, or `UNMAPPED` |
| `unmapped` | empty `codes`; the caller elicits |

First hit wins. Claude is asked only when the first three fail *and* stage 3
found near-misses worth judging — 2 of the 12 exit cases, in both of which the
right answer is `UNMAPPED`.

The model can never introduce a code stage 3 did not propose. That is enforced by
an `output_config.format` enum restricted to the candidate codes **and** by a
membership check in Python — not by prompt wording.

### Two invariants worth not breaking

**Title → code is one-to-many and must stay that way.**
`Machine Learning Engineer` legitimately maps to 15-1221.00, 15-1299.08 *and*
15-2051.00. Never collapse to one code and never take `codes[0]`; if you need a
single career, get it from `apply_backoff`, which reports `ambiguous` and
deliberately refuses to pick.

**`backoff_level` is recorded, never inferred.** It is set at the moment the
decision is made and surfaced in the UI explanation panel. Re-deriving it later
against a changed leaf set would produce a different answer than the one the
student was actually shown.

## Diagnostics

```bash
PYTHONPATH=src python dev/inspect_onet_release_contents.py   # what did we download
PYTHONPATH=src python dev/compare_release_code_overlap.py    # cross-release overlap
PYTHONPATH=src python dev/tune_fuzzy_match_threshold.py      # -> reports/fuzzy_threshold_sweep.csv
PYTHONPATH=src python dev/inspect_ece_title_coverage.py      # -> reports/ece_title_coverage.md
```

## Tests

```bash
python -m pytest
```

The three that matter most: `test_career_title_resolution.py` asserts the stage
reached as well as the codes, so a regression that pushes an exact match down
into the LLM stage fails loudly instead of quietly costing a model call per
title; `test_resolution_determinism.py` resolves the full exit table cold, warm
in-process, and warm from disk and requires byte-identical tuples;
`test_resolution_without_network.py` monkeypatches the client to raise and
requires every stage-1–3 case to still pass.
