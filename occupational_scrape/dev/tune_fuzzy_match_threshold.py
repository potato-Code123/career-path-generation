"""Diagnostic: sweep the stage-3 cosine threshold and write the evidence.

Writes ``dev/reports/fuzzy_threshold_sweep.csv``. The chosen value is then copied
by hand into ``config/fuzzy_match_settings.yaml``; nothing in ``src/`` reads this
script or its output. Re-run it when the O*NET release is bumped.

The probe set is split into three groups because a single "accuracy" number would
hide the tradeoff that actually matters:

``should_hit``   inputs a student would plausibly type that have a defensible ECE
                 answer in the index. Recall here is what a low threshold buys.
``should_miss``  inputs with no ECE answer. A fuzzy match on these is worse than
                 no match: it silently keys transcript statistics to a wrong
                 career instead of triggering elicitation.
``should_not_be_ece``
                 inputs whose right answer exists but is outside ECE. These
                 detect the specific failure of the matcher dragging an unrelated
                 title into the ECE namespace.

Run with the repo's venv:  PYTHONPATH=src python dev/tune_fuzzy_match_threshold.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from occupational_scrape.build_occupation_titles_index import load_title_index  # noqa: E402
from occupational_scrape.match_title_fuzzy import (  # noqa: E402
    FuzzyMatcher,
    load_fuzzy_settings,
)
from occupational_scrape.normalize_job_title import normalize_title  # noqa: E402

REPORT_PATH = REPO / "dev" / "reports" / "fuzzy_threshold_sweep.csv"

ECE_PREFIXES = ("17-2", "17-3", "15-12", "15-2")

SHOULD_HIT = [
    "chip designer",
    "digital design designer",          # what "RTL designer" expands to
    "asic design engineer",
    "hardware design engineer",
    "embedded systems engineer",
    "verification engineer",
    "analog design engineer",
    "power electronics engineer",
    "controls engineer",
    "signal processing engineer",
    "computer architect",
    "network engineer",
    "machine learning scientist",
    "robotics software engineer",
    "semiconductor process engineer",
]

SHOULD_MISS = [
    "asdfqwer",
    "zzzzzz",
    "qqqqqqqqqq",
    "xkcd",
    "blorptron specialist",
    "flurbish wrangler",
]

SHOULD_NOT_BE_ECE = [
    "quant",
    "prompt engineer",
    "dental hygienist",
    "line cook",
    "paralegal",
    "high school teacher",
]

THRESHOLDS = [round(0.20 + 0.025 * step, 3) for step in range(29)]  # 0.20 .. 0.90


def is_ece(code: str) -> bool:
    return code.startswith(ECE_PREFIXES)


def main() -> None:
    index = load_title_index()
    settings = load_fuzzy_settings()
    matcher = FuzzyMatcher(index, settings)

    # Score every probe once, then apply thresholds to the same scores.
    scored: dict[str, list[tuple[float, str]]] = {}
    for probe in SHOULD_HIT + SHOULD_MISS + SHOULD_NOT_BE_ECE:
        hits = matcher.match(probe, threshold=0.0, top_k=settings.top_k)
        scored[probe] = [(hit.score, hit.code) for hit in hits]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "threshold",
                "should_hit_n",
                "should_hit_recall",
                "should_hit_ece_recall",
                "should_miss_n",
                "should_miss_false_match_rate",
                "should_not_be_ece_n",
                "should_not_be_ece_leak_rate",
                "mean_candidates_per_hit",
                "notes",
            ]
        )
        for threshold in THRESHOLDS:
            hit_any = 0
            hit_ece = 0
            candidate_counts: list[int] = []
            for probe in SHOULD_HIT:
                kept = [pair for pair in scored[probe] if pair[0] >= threshold]
                if kept:
                    hit_any += 1
                    candidate_counts.append(len(kept))
                if any(is_ece(code) for _, code in kept):
                    hit_ece += 1

            false_match = sum(
                1 for probe in SHOULD_MISS if any(s >= threshold for s, _ in scored[probe])
            )
            leaked = sum(
                1
                for probe in SHOULD_NOT_BE_ECE
                if any(s >= threshold and is_ece(code) for s, code in scored[probe])
            )

            writer.writerow(
                [
                    threshold,
                    len(SHOULD_HIT),
                    round(hit_any / len(SHOULD_HIT), 3),
                    round(hit_ece / len(SHOULD_HIT), 3),
                    len(SHOULD_MISS),
                    round(false_match / len(SHOULD_MISS), 3),
                    len(SHOULD_NOT_BE_ECE),
                    round(leaked / len(SHOULD_NOT_BE_ECE), 3),
                    round(sum(candidate_counts) / len(candidate_counts), 2)
                    if candidate_counts
                    else 0.0,
                    "",
                ]
            )

    print(f"wrote {REPORT_PATH}")
    print("\nper-probe top match (for eyeballing where the cliff is):")
    for probe in SHOULD_HIT + SHOULD_NOT_BE_ECE + SHOULD_MISS:
        top = scored[probe][0] if scored[probe] else (0.0, "-")
        group = (
            "hit"
            if probe in SHOULD_HIT
            else ("not-ece" if probe in SHOULD_NOT_BE_ECE else "miss")
        )
        print(f"  {group:<8} {probe!r:38} {top[0]:.3f}  {top[1]}")
    print(f"\nnormalize_title sanity: 'RTL designer' -> {normalize_title('RTL designer')!r}")


if __name__ == "__main__":
    main()
