"""Append-only cache of career-title resolutions.

This file is what makes the career namespace reproducible. The statistics layer
computes lift as ``P(course | career) / P(course)``, keyed on a career code; if
the same input string resolved to a different code on Tuesday than on Monday,
every lift derived from it silently changes with no error and no diff. The cache
turns the LLM stage into a once-per-novel-title event rather than a
once-per-request one, so a resolution recorded today is replayed verbatim
forever.

The file is append-only. A resolution is never edited in place; a correction is a
new row for the same normalized key, and lookup takes the **last** matching row.
That keeps the history of what was believed when, which matters when a downstream
statistic has to be explained months later.

Columns
-------
``input_raw``               the string as the caller typed it
``normalized``              the lookup key, from ``normalize_job_title``
``codes``                   pipe-separated O*NET-SOC codes, possibly empty
``stage``                   exact | expanded | fuzzy | llm | unmapped
``resolved_utc``            when the row was written
``model_id``                the model that produced an ``llm`` row, else empty
``candidates_considered``   pipe-separated codes weighed before choosing

Deviation from the phase specification: ``candidates_considered`` is a seventh
column the spec's column list does not name. It has to be persisted, because the
spec also requires that resolving the same input from a warm cache file produce a
*byte-identical* ``Resolution`` tuple -- and ``candidates_considered`` is a field
of that tuple. Recomputing it on a cache hit would mean re-running the fuzzy
stage, which defeats the cache; defaulting it to the resolved codes would make
warm-cache tuples differ from cold-cache ones and fail the determinism test. The
two requirements are in direct conflict and the determinism one is load-bearing,
so the column is added rather than the guarantee weakened.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import CACHE_DIR

__all__ = [
    "CACHE_PATH",
    "FIELDNAMES",
    "CachedResolution",
    "ResolutionCache",
    "get_cache",
    "reset_cache",
]

CACHE_PATH = CACHE_DIR / "career_title_resolutions.csv"

FIELDNAMES = (
    "input_raw",
    "normalized",
    "codes",
    "stage",
    "resolved_utc",
    "model_id",
    "candidates_considered",
)

_SEPARATOR = "|"


def _join(codes: tuple[str, ...]) -> str:
    return _SEPARATOR.join(codes)


def _split(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part for part in value.split(_SEPARATOR) if part)


@dataclass(frozen=True)
class CachedResolution:
    """One persisted row, in primitive form.

    Deliberately not the resolver's ``Resolution`` dataclass: this module must not
    import the resolver, or the two form an import cycle.
    """

    input_raw: str
    normalized: str
    codes: tuple[str, ...]
    stage: str
    resolved_utc: str
    model_id: str
    candidates_considered: tuple[str, ...]


class ResolutionCache:
    """Read-through, append-on-write cache over a CSV file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CACHE_PATH
        self._by_normalized: dict[str, CachedResolution] = {}
        self._loaded = False

    def load(self) -> None:
        """Read the file into memory. Later rows override earlier ones."""
        self._by_normalized = {}
        if self.path.exists():
            with self.path.open(newline="") as handle:
                for row in csv.DictReader(handle):
                    record = CachedResolution(
                        input_raw=row.get("input_raw", ""),
                        normalized=row.get("normalized", ""),
                        codes=_split(row.get("codes")),
                        stage=row.get("stage", "unmapped"),
                        resolved_utc=row.get("resolved_utc", ""),
                        model_id=row.get("model_id", "") or "",
                        candidates_considered=_split(row.get("candidates_considered")),
                    )
                    self._by_normalized[record.normalized] = record
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def lookup(self, normalized: str) -> CachedResolution | None:
        self._ensure_loaded()
        return self._by_normalized.get(normalized)

    def record(
        self,
        *,
        input_raw: str,
        normalized: str,
        codes: tuple[str, ...],
        stage: str,
        candidates_considered: tuple[str, ...],
        model_id: str = "",
        resolved_utc: str | None = None,
    ) -> CachedResolution:
        """Append a resolution and return the record as it will be replayed.

        The returned record is what a later cache hit will produce, so callers
        should build their ``Resolution`` from this rather than from the values
        they passed in. That is what keeps a cold run and a warm run identical.
        """
        self._ensure_loaded()
        record = CachedResolution(
            input_raw=input_raw,
            normalized=normalized,
            codes=tuple(codes),
            stage=stage,
            resolved_utc=resolved_utc
            or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            model_id=model_id or "",
            candidates_considered=tuple(candidates_considered),
        )
        self._append(record)
        self._by_normalized[normalized] = record
        return record

    def _append(self, record: CachedResolution) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            if needs_header:
                writer.writeheader()
            writer.writerow(
                {
                    "input_raw": record.input_raw,
                    "normalized": record.normalized,
                    "codes": _join(record.codes),
                    "stage": record.stage,
                    "resolved_utc": record.resolved_utc,
                    "model_id": record.model_id,
                    "candidates_considered": _join(record.candidates_considered),
                }
            )

    def clear_memory(self) -> None:
        """Drop the in-memory view, keeping the file.

        Exists for ``tests/test_resolution_determinism.py``, which has to prove a
        warm file replays identically to a live in-process cache.
        """
        self._by_normalized = {}
        self._loaded = False


_DEFAULT: ResolutionCache | None = None


def get_cache(path: Path | None = None) -> ResolutionCache:
    """Process-wide cache instance."""
    global _DEFAULT
    if _DEFAULT is None or (path is not None and _DEFAULT.path != path):
        _DEFAULT = ResolutionCache(path)
    return _DEFAULT


def reset_cache(path: Path | None = None) -> ResolutionCache:
    """Rebuild the process-wide instance, e.g. to point tests at a temp file."""
    global _DEFAULT
    _DEFAULT = ResolutionCache(path)
    return _DEFAULT
