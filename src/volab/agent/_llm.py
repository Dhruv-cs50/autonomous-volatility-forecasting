"""The one place in the agent package that talks to a language-model provider.

Phase 5 needs an LLM API key that only the user can provision. The point of this module is that
the key gates *live runs only*: every other part of the agent layer takes a ``Completer`` rather
than calling out itself, so it is built, tested and merged with no credential at all. This is
the pattern ``volab.ingest._http`` already uses for HTTP, and the sentence it states is the one
copied here: ``default_completer`` is the only implementation that touches the wire, and no test
uses it.

``Completer`` is a ``Callable`` alias and not a ``Protocol`` for the reason ``_http.py`` gave for
``Transport = Callable[[str], bytes]``: a fake is then any function or any object with
``__call__``, with no class to inherit from and nothing for a mypy-strict test double to satisfy
beyond the signature.

**Unknown is not zero.** ``Completion.input_tokens`` and ``output_tokens`` are ``None`` when the
provider did not report them, never 0 (the R70 distinction). A count of 0 that really means
"unmeasured" would flow into any cost total as a confident number, which is the kind of figure
this repository refuses. ``FakeCompleter`` therefore reports ``None`` too: it measured nothing.

**What this module does not cover.** The wire format below (Anthropic's Messages API: a
``POST {base_url}/v1/messages`` with ``x-api-key`` and ``anthropic-version`` headers, answered by
a ``content`` list of typed blocks, a ``stop_reason`` and a ``usage`` object) was written from
the published shape; the first two real-store sprints are the only runs against a provider.
``completion_from_response`` is tested against bodies shaped like that documentation. There is no
retry: unlike a public data endpoint, a model call that fails is raised so the sprint records it,
rather than quietly spending budget a second time.

**A reply counts only if it ended on its own** (R226). The default model thinks before it
answers, and thinking counts toward ``max_tokens``. A reply that hit the cap still carries text,
cut mid-sentence, and the first real-store sprint stored three such replies as if they were
whole. So ``stop_reason`` must be ``end_turn`` or ``stop_sequence``; anything else, including a
safety ``refusal`` and an absent stop reason, is ``ParseRefused`` with the stop reason and the
content block types in the message. The cap is sized from measured whole replies.

The module imports nothing first-party except ``volab.errors``, so it sits inside the R192 import
fence, and it reads no environment variable at import time.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from volab.errors import ConfigError, ParseRefused

# The project's cheap scheduled-run model. The only place its id is written down.
DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_BASE_URL = "https://api.anthropic.com"
API_KEY_VARIABLE = "VOLAB_LLM_API_KEY"
BASE_URL_VARIABLE = "VOLAB_LLM_BASE_URL"
MODEL_VARIABLE = "VOLAB_LLM_MODEL"

_API_VERSION = "2023-06-01"
_TIMEOUT_SECONDS = 60.0

# Four times the largest whole reply measured (2057 output tokens, the Critic's, thinking
# included); the first real-store sprint's 1024 cut every role but the Configurator short (R226).
# A cap is a ceiling, not a charge: only the tokens a reply uses are billed.
DEFAULT_MAX_TOKENS = 8192
# The stop reasons of a reply the model finished; any other leaves the text incomplete or absent.
_FINISHED = ("end_turn", "stop_sequence")


@dataclass(frozen=True)
class Prompt:
    """One request: a system instruction, a user turn, and a cap on the reply length."""

    system: str
    user: str
    max_tokens: int = DEFAULT_MAX_TOKENS


@dataclass(frozen=True)
class Completion:
    """One reply. Token counts are ``None`` when the provider did not report them, never 0."""

    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


Completer = Callable[[Prompt], Completion]


def _refuse(message: str, cause: BaseException | None = None) -> ParseRefused:
    refusal = ParseRefused(message)
    refusal.__cause__ = cause
    return refusal


def _token_count(usage: dict[str, Any], key: str) -> int | None:
    if key not in usage:
        return None
    value = usage[key]
    # ``bool`` is an ``int`` subclass; ``True`` is not a count of one.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _refuse(f"usage.{key} is {value!r}, not an integer")
    return value


def completion_from_response(body: bytes) -> Completion:
    """Read a provider response body into a ``Completion``, or raise ``ParseRefused``.

    The wire-touching function is a thin shell around this one so that everything which can be
    wrong about a *response* is testable from bytes. Text blocks are concatenated and other block
    types are skipped; a reply with no text block at all is refused, since an empty string would
    be indistinguishable from a model that chose to say nothing. Before that, a reply whose
    ``stop_reason`` is not ``end_turn`` or ``stop_sequence`` is refused, text or not, since its
    text is cut short or absent (R226). A missing ``usage`` object, or a missing field inside it,
    gives ``None`` for that count.
    """
    try:
        payload = json.loads(body)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
        raise _refuse("provider response is not JSON", exc) from exc
    if not isinstance(payload, dict):
        raise _refuse(f"provider response is a {type(payload).__name__}, not an object")
    model = payload.get("model")
    if not isinstance(model, str):
        raise _refuse("provider response has no string 'model'")
    content = payload.get("content")
    if not isinstance(content, list):
        raise _refuse("provider response has no 'content' list")
    blocks = [block.get("type") if isinstance(block, dict) else None for block in content]
    stop_reason = payload.get("stop_reason")
    if stop_reason not in _FINISHED:
        details = payload.get("stop_details")
        category = details.get("category") if isinstance(details, dict) else None
        raise _refuse(
            f"provider reply did not finish: stop_reason {stop_reason!r}"
            + (f" (category {category!r})" if category is not None else "")
            + f", content blocks {blocks!r}"
        )
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise _refuse("a text block's 'text' is not a string")
            texts.append(text)
    if not texts:
        raise _refuse(f"provider response holds no text block: content blocks {blocks!r}")
    usage = payload.get("usage")
    if usage is None:
        return Completion("".join(texts), model, None, None)
    if not isinstance(usage, dict):
        raise _refuse("provider response 'usage' is not an object")
    return Completion(
        "".join(texts),
        model,
        _token_count(usage, "input_tokens"),
        _token_count(usage, "output_tokens"),
    )


def default_completer(
    *, model: str = DEFAULT_MODEL, api_key: str, base_url: str = DEFAULT_BASE_URL
) -> Completer:
    """The ONE function here that touches the wire. No test uses it.

    Returns a ``Completer`` bound to one model and key. A transport failure (timeout, connection
    error, non-2xx status) is raised as ``RuntimeError`` in the manner of ``default_transport``;
    a response that arrives and cannot be read is ``ParseRefused`` from
    ``completion_from_response``. Executed only by the live real-store sprints.
    """

    def complete(prompt: Prompt) -> Completion:
        request_body = json.dumps(
            {
                "model": model,
                "max_tokens": prompt.max_tokens,
                "system": prompt.system,
                "messages": [{"role": "user", "content": prompt.user}],
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310 - https URL built from configuration
            base_url.rstrip("/") + "/v1/messages",
            data=request_body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": _API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                body: bytes = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"completion request failed: {exc}") from exc
        return completion_from_response(body)

    return complete


def completer_from_environment() -> Completer:
    """Build the live completer from ``VOLAB_LLM_API_KEY``; called only from ``__main__``.

    Reads the environment when *called*, never at import. An absent or blank key raises
    ``ConfigError`` naming the variable and never quoting a value. ``VOLAB_LLM_MODEL`` and
    ``VOLAB_LLM_BASE_URL`` are optional and default to ``DEFAULT_MODEL`` and
    ``DEFAULT_BASE_URL``. Building the callable makes no request.
    """
    api_key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not api_key:
        raise ConfigError(
            f"{API_KEY_VARIABLE} is not set; a live agent run needs a provider key. "
            "Everything except a live run works without one."
        )
    return default_completer(
        model=os.environ.get(MODEL_VARIABLE, "").strip() or DEFAULT_MODEL,
        api_key=api_key,
        base_url=os.environ.get(BASE_URL_VARIABLE, "").strip() or DEFAULT_BASE_URL,
    )


class FakeCompleter:
    """A scripted completer: hands back queued texts in order and records the prompts it saw.

    ``prompts`` holds exactly the prompts that were answered, so a test can assert what the
    agent actually asked. A call beyond the script raises ``RuntimeError`` and records nothing,
    because a fake that invented a reply would let a test pass on text nobody scripted. Token
    counts are ``None``: the fake measured nothing, and 0 would claim it did.
    """

    def __init__(self, texts: Sequence[str], *, model: str = "fake-model") -> None:
        self._queue = list(texts)
        self._model = model
        self._seen: list[Prompt] = []

    @property
    def prompts(self) -> tuple[Prompt, ...]:
        return tuple(self._seen)

    def __call__(self, prompt: Prompt) -> Completion:
        if not self._queue:
            raise RuntimeError("FakeCompleter exhausted: more prompts than scripted replies")
        self._seen.append(prompt)
        return Completion(self._queue.pop(0), self._model, None, None)
