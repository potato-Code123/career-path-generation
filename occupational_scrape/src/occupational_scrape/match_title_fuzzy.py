"""Stage 3 of the cascade: character n-gram TF-IDF nearest-title search.

Fits a TF-IDF vectorizer over character n-grams of every distinct
``normalized_title`` in the index, then scores a query by cosine similarity.
Character n-grams rather than word tokens because the failure mode being covered
is morphological -- ``chip designer`` against ``chip design engineer``,
``fpga design`` against ``fpga designer`` -- not synonymy.

Everything about the search is deterministic and must stay that way, because a
career code that changes between runs silently corrupts every lift statistic
keyed on it:

* the fitting corpus is the sorted list of distinct keys, so the vocabulary and
  therefore the IDF weights do not depend on row order in the parquet file;
* ties are broken on ``(-score, normalized_title, code)``, never on argsort
  order, which is unstable for equal values;
* the threshold and ``k`` come from ``config/fuzzy_match_settings.yaml``, not
  from constants here, so the value that produced a cached resolution is
  recoverable from the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer

from . import CONFIG_DIR
from .build_occupation_titles_index import TitleIndex, load_title_index
from .normalize_job_title import normalize_title

__all__ = [
    "SETTINGS_PATH",
    "FuzzySettings",
    "FuzzyCandidate",
    "FuzzyMatcher",
    "load_fuzzy_settings",
    "get_matcher",
]

SETTINGS_PATH = CONFIG_DIR / "fuzzy_match_settings.yaml"


@dataclass(frozen=True)
class FuzzySettings:
    threshold: float
    llm_candidate_floor: float
    top_k: int
    ngram_range: tuple[int, int]
    analyzer: str
    lowercase: bool


@dataclass(frozen=True)
class FuzzyCandidate:
    code: str
    normalized_title: str
    score: float


def load_fuzzy_settings(path: Path | None = None) -> FuzzySettings:
    raw = yaml.safe_load((path or SETTINGS_PATH).read_text()) or {}
    low, high = raw["ngram_range"]
    return FuzzySettings(
        threshold=float(raw["threshold"]),
        llm_candidate_floor=float(raw.get("llm_candidate_floor", raw["threshold"])),
        top_k=int(raw["top_k"]),
        ngram_range=(int(low), int(high)),
        analyzer=str(raw.get("analyzer", "char")),
        lowercase=bool(raw.get("lowercase", True)),
    )


class FuzzyMatcher:
    """A fitted vectorizer over the title index, reusable across queries."""

    def __init__(self, index: TitleIndex, settings: FuzzySettings) -> None:
        self.index = index
        self.settings = settings
        self.keys: list[str] = index.normalized_titles  # already sorted
        self.vectorizer = TfidfVectorizer(
            analyzer=settings.analyzer,
            ngram_range=settings.ngram_range,
            lowercase=settings.lowercase,
        )
        # L2-normalized by default, so a dot product is the cosine similarity.
        self.matrix = self.vectorizer.fit_transform(self.keys)

    def score_query(self, normalized_query: str) -> np.ndarray:
        vector = self.vectorizer.transform([normalized_query])
        return (self.matrix @ vector.T).toarray().ravel()

    def match(
        self,
        raw_or_normalized: str,
        *,
        already_normalized: bool = False,
        threshold: float | None = None,
        top_k: int | None = None,
    ) -> tuple[FuzzyCandidate, ...]:
        """Top-k codes whose best-matching index key scores at or above threshold.

        ``k`` counts *codes*, not titles: one title can carry several codes, and
        the LLM stage needs a bounded candidate list of codes to choose from.
        """
        query = raw_or_normalized if already_normalized else normalize_title(raw_or_normalized)
        if not query:
            return ()

        floor = self.settings.threshold if threshold is None else threshold
        limit = self.settings.top_k if top_k is None else top_k

        scores = self.score_query(query)
        above = np.flatnonzero(scores >= floor)
        if above.size == 0:
            return ()

        # Explicit deterministic ordering; np.argsort alone is not stable across
        # equal scores in a way we want to depend on.
        ranked = sorted(
            ((float(scores[position]), self.keys[position]) for position in above),
            key=lambda pair: (-pair[0], pair[1]),
        )

        candidates: list[FuzzyCandidate] = []
        seen: set[str] = set()
        for score, key in ranked:
            for code in self.index.lookup_exact(key):
                if code in seen:
                    continue
                seen.add(code)
                candidates.append(FuzzyCandidate(code=code, normalized_title=key, score=score))
                if len(candidates) >= limit:
                    return tuple(candidates)
        return tuple(candidates)


    def match_many(
        self,
        queries: tuple[str, ...],
        *,
        already_normalized: bool = True,
        threshold: float | None = None,
        top_k: int | None = None,
    ) -> tuple[FuzzyCandidate, ...]:
        """Pool matches across several phrasings of the same input.

        Stage 2's expansions are the bridge between a student's vocabulary and
        O*NET's, so stage 3 searches them too rather than throwing them away when
        the exact retry misses. ``RTL designer`` is the motivating case: the raw
        form's best neighbours are the useless bare title ``designer`` at 0.673,
        while its expansion ``Digital Design designer`` reaches
        ``digital design engineer`` (17-2061.00) at 0.705. Pooling and re-ranking
        by score puts the useful candidate inside the top ``k``; searching the raw
        form alone would bury it.

        Deduplicated by code, keeping each code's highest score.
        """
        floor = self.settings.threshold if threshold is None else threshold
        limit = self.settings.top_k if top_k is None else top_k

        best: dict[str, FuzzyCandidate] = {}
        for query in queries:
            for candidate in self.match(
                query,
                already_normalized=already_normalized,
                threshold=floor,
                top_k=limit,
            ):
                existing = best.get(candidate.code)
                if existing is None or candidate.score > existing.score:
                    best[candidate.code] = candidate

        ranked = sorted(
            best.values(),
            key=lambda candidate: (-candidate.score, candidate.normalized_title, candidate.code),
        )
        return tuple(ranked[:limit])


@lru_cache(maxsize=1)
def get_matcher() -> FuzzyMatcher:
    """Process-wide matcher. Fitting is expensive; the result is immutable."""
    return FuzzyMatcher(load_title_index(), load_fuzzy_settings())
