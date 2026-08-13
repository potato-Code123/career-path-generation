"""Stage 4 of the cascade: Claude picks one code from the stage-3 candidate set.

The model is a *selector*, never a generator. It is shown the candidates that
stage 3 surfaced -- each with its O*NET occupation description -- and must answer
with one of their exact codes or the literal string ``UNMAPPED``. Anything else is
treated as ``UNMAPPED``.

The containment guarantee is enforced twice, and neither layer is the prompt:

1. ``output_config.format`` constrains the response to a JSON schema whose
   ``code`` field is an ``enum`` of exactly the candidate codes plus ``UNMAPPED``.
   The model cannot emit a code that was not offered.
2. :func:`select_candidate` re-checks membership in Python before returning.

Prompt wording is not a security boundary. If the schema were dropped tomorrow,
layer 2 would still make it impossible for this stage to introduce a code that
stage 3 did not propose.

Deviation from the phase specification -- ``temperature 0``
-----------------------------------------------------------
The spec asks for temperature 0. That parameter no longer exists on the current
Claude models: ``temperature``, ``top_p`` and ``top_k`` were removed on Claude
Opus 5 / Opus 4.8 / 4.7 and Sonnet 5, and sending any of them returns HTTP 400.
There is no replacement knob -- sampling is not caller-configurable on these
models.

What the spec actually wanted from temperature 0 is reproducibility of the career
namespace, and that is delivered by a stronger mechanism the spec itself
specifies: :mod:`occupational_scrape.resolution_cache` makes this call run once
per novel title, ever. A cached resolution is replayed byte-identically no matter
what the model would say on a re-ask, and the ``model_id`` that produced it is
recorded alongside it. Determinism is a property of the cache, not of the
sampler. Within a single call, the enum-constrained output and the Python-side
membership check bound the blast radius of any residual variation to "one of the
candidates stage 3 already proposed, or UNMAPPED".

If a future model reinstates a determinism parameter, set it in
:data:`REQUEST_OVERRIDES` rather than reintroducing ``temperature`` here.

Client
------
There is no existing Claude client in this repository to call through -- the
sibling application is TypeScript and has no Anthropic integration -- so this
module owns a thin one. It is injectable: pass any object with a
``complete(prompt, schema) -> str`` method to :func:`select_candidate`, which is
how the tests substitute a stub or force a failure.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "UNMAPPED",
    "MODEL_ID",
    "REQUEST_OVERRIDES",
    "LlmUnavailable",
    "LlmClient",
    "AnthropicSelectionClient",
    "Candidate",
    "build_prompt",
    "select_candidate",
    "get_default_client",
]

UNMAPPED = "UNMAPPED"

MODEL_ID = os.environ.get("OCCSCRAPE_LLM_MODEL", "claude-opus-5")
"""Recorded in every cache row so a resolution is attributable to a model."""

REQUEST_OVERRIDES: dict[str, object] = {
    # Selection from a bounded list is a shallow task; low effort keeps the call
    # cheap and short. Sampling parameters are deliberately absent -- see the
    # module docstring.
    "output_config": {"effort": "low"},
}

MAX_TOKENS = 2048

_SYSTEM_PROMPT = (
    "You map a student's free-text career goal onto the O*NET-SOC occupation "
    "taxonomy for an Electrical and Computer Engineering department.\n\n"
    "You are given a fixed list of candidate occupations. Choose the single "
    "candidate that best matches the stated career goal, or answer UNMAPPED.\n\n"
    "Rules:\n"
    "- You may only answer with a code that appears in the candidate list, or "
    "the literal string UNMAPPED.\n"
    "- Answer UNMAPPED whenever no candidate is a genuine match. A wrong match is "
    "worse than no match: an unmapped goal is resolved by asking the student, "
    "while a wrong code silently attributes their coursework to another career.\n"
    "- Judge on the occupation description, not on surface word overlap with the "
    "title. A title that shares words with the input but describes different work "
    "is not a match.\n"
    "- Do not infer a match from the department context. If the goal is not an "
    "engineering or computing role, UNMAPPED is the correct answer."
)


class LlmUnavailable(RuntimeError):
    """No usable client: SDK missing, credentials absent, or the call failed.

    Callers treat this as "stage 4 did not run" and fall through to ``unmapped``.
    It is never converted into a code.
    """


@dataclass(frozen=True)
class Candidate:
    code: str
    title: str
    description: str


class LlmClient(Protocol):
    def complete(self, prompt: str, schema: dict) -> str:  # pragma: no cover - protocol
        ...


def build_prompt(input_raw: str, candidates: tuple[Candidate, ...]) -> str:
    """Render the selection prompt. Deterministic given the same candidate order."""
    lines = [
        f"Career goal as written by the student: {input_raw!r}",
        "",
        "Candidate occupations:",
    ]
    for candidate in candidates:
        description = " ".join(candidate.description.split())
        lines.append(f"\n[{candidate.code}] {candidate.title}\n    {description}")
    lines += [
        "",
        f"Answer with one candidate code from the list above, or {UNMAPPED}.",
    ]
    return "\n".join(lines)


def _response_schema(candidates: tuple[Candidate, ...]) -> dict:
    return {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "enum": [candidate.code for candidate in candidates] + [UNMAPPED],
            }
        },
        "required": ["code"],
        "additionalProperties": False,
    }


class AnthropicSelectionClient:
    """Default client, built on the official Anthropic SDK.

    Constructed lazily so that importing this module never requires the SDK or
    an API key. Stages 1-3 of the cascade and every build script must work on a
    machine that has neither.
    """

    def __init__(self, model_id: str = MODEL_ID) -> None:
        try:
            import anthropic
        except ImportError as error:  # pragma: no cover - environment dependent
            raise LlmUnavailable(
                "the `anthropic` package is not installed; "
                "install the optional 'llm' extra to enable stage 4"
            ) from error
        self._anthropic = anthropic
        self.model_id = model_id
        try:
            self._client = anthropic.Anthropic()
        except Exception as error:  # pragma: no cover - environment dependent
            raise LlmUnavailable(f"could not construct an Anthropic client: {error}") from error

    def complete(self, prompt: str, schema: dict) -> str:
        try:
            response = self._client.messages.create(
                model=self.model_id,
                max_tokens=MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={**REQUEST_OVERRIDES.get("output_config", {}),
                               "format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as error:  # pragma: no cover - network dependent
            raise LlmUnavailable(f"Claude call failed: {error}") from error

        if getattr(response, "stop_reason", None) == "refusal":
            raise LlmUnavailable("Claude declined the request")
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise LlmUnavailable("Claude returned no text block")


def get_default_client() -> LlmClient:
    """Build the default client, raising :class:`LlmUnavailable` if impossible."""
    return AnthropicSelectionClient()


def select_candidate(
    input_raw: str,
    candidates: tuple[Candidate, ...],
    client: LlmClient,
) -> str | None:
    """Return the chosen code, or ``None`` for UNMAPPED / any invalid answer.

    Raises :class:`LlmUnavailable` if the client could not be reached; that is
    distinct from the model deliberately answering UNMAPPED.
    """
    if not candidates:
        return None

    allowed = {candidate.code for candidate in candidates}
    schema = _response_schema(candidates)
    answer = client.complete(build_prompt(input_raw, candidates), schema)

    code = _extract_code(answer)
    if code is None or code == UNMAPPED:
        return None
    # Enforcement, not prompt wording: a code outside the offered set is discarded.
    if code not in allowed:
        return None
    return code


def _extract_code(answer: str) -> str | None:
    """Pull the code out of a structured-output response, tolerating plain text."""
    if answer is None:
        return None
    text = answer.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text.split()[0].strip().strip('".,')
    if isinstance(payload, dict):
        value = payload.get("code")
        return None if value is None else str(value).strip()
    return None
