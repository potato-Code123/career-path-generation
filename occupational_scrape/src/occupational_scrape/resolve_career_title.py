"""The five-stage career-title resolution cascade.

``exact -> expanded -> fuzzy -> llm -> unmapped``. First hit wins.

Every resolution is keyed on the normalized input and cached before it is
returned, and the returned :class:`Resolution` is rebuilt *from the cached
record* rather than from the in-flight values. That last detail is what makes a
cold run and a warm-cache run produce byte-identical tuples: there is exactly one
construction path, and it runs on both.

Stage 3 / stage 4 boundary
--------------------------
Stage 3 hits when a candidate clears ``threshold``. Only when nothing clears it
is Claude asked -- and it is then shown the near-misses gathered at the lower
``llm_candidate_floor``, so "the stage-3 candidate set" is non-empty at the
moment stage 4 needs it. If nothing clears even that floor, the input is noise
and the answer is ``unmapped`` without spending a call.

What is deliberately *not* cached
---------------------------------
A stage-4 attempt that failed because Claude was unreachable is not written to
the cache. Caching it would let a transient network failure permanently record a
career goal as unmapped, which is exactly the kind of silent, sticky corruption
this phase exists to prevent. A deliberate ``UNMAPPED`` answer from the model
*is* cached -- that is a real judgement, not a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .build_occupation_titles_index import TitleIndex, load_title_index
from .expand_title_abbreviations import expand_tokens
from .fetch_source_files import read_onet_table
from .match_title_fuzzy import FuzzyMatcher, get_matcher
from .normalize_job_title import normalize_title
from .resolution_cache import CachedResolution, ResolutionCache, get_cache
from .select_candidate_via_llm import (
    MODEL_ID,
    Candidate,
    LlmClient,
    LlmUnavailable,
    get_default_client,
    select_candidate,
)

__all__ = [
    "Resolution",
    "STAGES",
    "resolve_career_title",
    "occupation_descriptions",
]

STAGES = ("exact", "expanded", "fuzzy", "llm", "unmapped")


@dataclass(frozen=True)
class Resolution:
    input_raw: str
    codes: tuple[str, ...]  # may be empty
    stage: str  # exact | expanded | fuzzy | llm | unmapped
    candidates_considered: tuple[str, ...]
    backoff_level: str | None = None  # set by apply_career_backoff


@lru_cache(maxsize=1)
def occupation_descriptions() -> dict[str, tuple[str, str]]:
    """``code -> (title, description)`` from the pinned O*NET release."""
    frame = read_onet_table("occupation_data")
    return {
        str(code): (str(title), str(description))
        for code, title, description in zip(
            frame["O*NET-SOC Code"], frame["Title"], frame["Description"]
        )
    }


def _from_record(record: CachedResolution) -> Resolution:
    """The single construction path for a returned Resolution."""
    return Resolution(
        input_raw=record.input_raw,
        codes=record.codes,
        stage=record.stage,
        candidates_considered=record.candidates_considered,
        backoff_level=None,
    )


def _exact(index: TitleIndex, normalized: str) -> tuple[str, ...]:
    return index.lookup_exact(normalized)


def _expanded(index: TitleIndex, variants: tuple[str, ...]) -> tuple[str, ...]:
    """Retry stage 1 on each expansion, in the fixed order stage 2 produced."""
    for variant in variants:
        codes = index.lookup_exact(normalize_title(variant))
        if codes:
            return codes
    return ()


def resolve_career_title(
    input_raw: str,
    *,
    index: TitleIndex | None = None,
    matcher: FuzzyMatcher | None = None,
    cache: ResolutionCache | None = None,
    client: LlmClient | None = None,
    use_llm: bool = True,
) -> Resolution:
    """Resolve a free-text career goal to a set of O*NET-SOC codes.

    ``client`` is injectable so tests can supply a stub or force a failure. When
    it is ``None`` a default Anthropic client is constructed lazily, and only if
    the cascade actually reaches stage 4.
    """
    index = index if index is not None else load_title_index()
    matcher = matcher if matcher is not None else get_matcher()
    cache = cache if cache is not None else get_cache()

    normalized = normalize_title(input_raw)
    if not normalized:
        return Resolution(input_raw=input_raw, codes=(), stage="unmapped", candidates_considered=())

    cached = cache.lookup(normalized)
    if cached is not None:
        return _from_record(cached)

    def finish(
        codes: tuple[str, ...],
        stage: str,
        candidates: tuple[str, ...],
        model_id: str = "",
    ) -> Resolution:
        record = cache.record(
            input_raw=input_raw,
            normalized=normalized,
            codes=codes,
            stage=stage,
            candidates_considered=candidates,
            model_id=model_id,
        )
        return _from_record(record)

    # Stage 1 -- exact
    codes = _exact(index, normalized)
    if codes:
        return finish(codes, "exact", codes)

    # Stage 2 -- expanded
    variants = expand_tokens(input_raw)
    codes = _expanded(index, variants)
    if codes:
        return finish(codes, "expanded", codes)

    # Stage 3 -- fuzzy over the raw form and every expansion
    queries = (normalized, *(normalize_title(variant) for variant in variants))
    queries = tuple(dict.fromkeys(query for query in queries if query))

    accepted = matcher.match_many(queries, threshold=matcher.settings.threshold)
    if accepted:
        codes = tuple(candidate.code for candidate in accepted)
        return finish(codes, "fuzzy", codes)

    # Stage 4 -- Claude selects from the near-misses
    near_misses = matcher.match_many(queries, threshold=matcher.settings.llm_candidate_floor)
    considered = tuple(candidate.code for candidate in near_misses)
    if not considered or not use_llm:
        return finish((), "unmapped", considered)

    descriptions = occupation_descriptions()
    candidates = tuple(
        Candidate(
            code=candidate.code,
            title=descriptions.get(candidate.code, (candidate.normalized_title, ""))[0],
            description=descriptions.get(candidate.code, ("", ""))[1],
        )
        for candidate in near_misses
    )

    try:
        active_client = client if client is not None else get_default_client()
        chosen = select_candidate(input_raw, candidates, active_client)
    except LlmUnavailable:
        # Not cached on purpose: a transient outage must not permanently pin this
        # input to `unmapped`. Recomputed identically on the next call.
        return Resolution(
            input_raw=input_raw,
            codes=(),
            stage="unmapped",
            candidates_considered=considered,
        )

    if chosen is None:
        return finish((), "unmapped", considered, model_id=MODEL_ID)
    return finish((chosen,), "llm", considered, model_id=MODEL_ID)


def main() -> None:  # pragma: no cover - convenience entry point
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m occupational_scrape.resolve_career_title <title>...")
    for argument in sys.argv[1:]:
        resolution = resolve_career_title(argument)
        print(f"{argument!r} -> stage={resolution.stage} codes={resolution.codes}")


if __name__ == "__main__":  # pragma: no cover
    main()
