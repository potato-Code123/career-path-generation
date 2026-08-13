"""The career namespace must be reproducible across runs and across cache states.

Every lift the statistics layer computes is keyed on a career code. If the same
input string resolves differently between runs, every statistic derived from it
changes with no error raised and no diff to inspect. These tests are the guard on
that property.

Three runs, per the phase specification:

1. cold -- empty cache, everything computed
2. warm in-process -- same cache object, served from memory
3. warm from disk -- in-memory view cleared, same CSV file re-read

All three must produce byte-identical ``Resolution`` tuples.
"""

from __future__ import annotations

import dataclasses

from conftest import CASES, StubLlmClient

from occupational_scrape.apply_career_backoff import apply_backoff
from occupational_scrape.resolution_cache import ResolutionCache
from occupational_scrape.resolve_career_title import resolve_career_title


def _resolve_all(cache: ResolutionCache, title_index, matcher, client):
    return tuple(
        resolve_career_title(
            case.input_raw,
            index=title_index,
            matcher=matcher,
            cache=cache,
            client=client,
        )
        for case in CASES
    )


def test_three_runs_are_byte_identical(title_index, matcher, temp_cache) -> None:
    client = StubLlmClient()

    cold = _resolve_all(temp_cache, title_index, matcher, client)
    warm_memory = _resolve_all(temp_cache, title_index, matcher, client)

    temp_cache.clear_memory()
    warm_disk = _resolve_all(temp_cache, title_index, matcher, client)

    assert cold == warm_memory
    assert cold == warm_disk

    # Field-by-field, so a failure names the field rather than the whole tuple.
    for first, second, third in zip(cold, warm_memory, warm_disk):
        for field in dataclasses.fields(first):
            name = field.name
            assert getattr(first, name) == getattr(second, name), name
            assert getattr(first, name) == getattr(third, name), name


def test_a_fresh_cache_object_over_the_same_file_agrees(
    title_index, matcher, temp_cache
) -> None:
    client = StubLlmClient()
    cold = _resolve_all(temp_cache, title_index, matcher, client)

    reopened = ResolutionCache(temp_cache.path)
    warm = _resolve_all(reopened, title_index, matcher, client)
    assert cold == warm


def test_the_llm_is_called_once_per_novel_title_not_once_per_request(
    title_index, matcher, temp_cache
) -> None:
    client = StubLlmClient()

    _resolve_all(temp_cache, title_index, matcher, client)
    after_first = len(client.calls)

    _resolve_all(temp_cache, title_index, matcher, client)
    temp_cache.clear_memory()
    _resolve_all(temp_cache, title_index, matcher, client)

    assert len(client.calls) == after_first, (
        "the LLM was re-invoked for a title already in the cache; the statistics "
        "layer would no longer be reproducible"
    )


def test_repeated_resolution_of_one_title_is_stable(title_index, matcher, temp_cache) -> None:
    client = StubLlmClient()
    results = [
        resolve_career_title(
            "RTL designer", index=title_index, matcher=matcher, cache=temp_cache, client=client
        )
        for _ in range(5)
    ]
    assert len(set(results)) == 1


def test_backoff_is_deterministic(hierarchy, title_index, matcher, temp_cache) -> None:
    client = StubLlmClient()
    resolutions = _resolve_all(temp_cache, title_index, matcher, client)
    first = tuple(apply_backoff(resolution, hierarchy=hierarchy) for resolution in resolutions)
    second = tuple(apply_backoff(resolution, hierarchy=hierarchy) for resolution in resolutions)
    assert first == second


def test_cache_file_grows_only_by_append(title_index, matcher, temp_cache) -> None:
    client = StubLlmClient()
    _resolve_all(temp_cache, title_index, matcher, client)
    first_bytes = temp_cache.path.read_bytes()

    _resolve_all(temp_cache, title_index, matcher, client)
    second_bytes = temp_cache.path.read_bytes()

    assert second_bytes.startswith(first_bytes), "existing cache rows were rewritten"
    assert second_bytes == first_bytes, "a cache hit wrote a redundant row"


def test_fuzzy_ranking_is_stable_across_repeated_queries(matcher) -> None:
    runs = [matcher.match("chip designer") for _ in range(5)]
    assert len(set(runs)) == 1
