"""The LLM port (``volab.agent._llm``): everything about it that needs no key.

Nothing here touches a network. ``default_completer`` is never called; its response parsing is
tested through ``completion_from_response``, the module-level function it delegates to. The
refusal of ``completer_from_environment`` with no key is the one piece of evidence this phase
holds about the keyed path.
"""

from __future__ import annotations

import json

import pytest

from volab.agent import _llm
from volab.agent._llm import (
    DEFAULT_MODEL,
    Completion,
    FakeCompleter,
    Prompt,
    completer_from_environment,
    completion_from_response,
)
from volab.errors import ConfigError, ParseRefused, VolabError


def _body(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def test_a_well_formed_response_becomes_a_completion_with_reported_token_counts() -> None:
    result = completion_from_response(_body())
    assert result == Completion(
        text="hello", model="claude-haiku-5-5", input_tokens=12, output_tokens=3
    )


def test_an_absent_usage_block_gives_none_token_counts_and_never_zero() -> None:
    payload = json.loads(_body())
    del payload["usage"]
    result = completion_from_response(json.dumps(payload).encode())
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_a_usage_block_missing_one_field_gives_none_for_that_field_only() -> None:
    result = completion_from_response(_body(usage={"input_tokens": 7}))
    assert result.input_tokens == 7
    assert result.output_tokens is None


def test_a_reported_zero_token_count_stays_zero() -> None:
    result = completion_from_response(_body(usage={"input_tokens": 0, "output_tokens": 0}))
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_text_blocks_are_concatenated_and_other_block_types_are_skipped() -> None:
    content = [
        {"type": "text", "text": "a"},
        {"type": "tool_use", "id": "x"},
        {"type": "text", "text": "b"},
    ]
    assert completion_from_response(_body(content=content)).text == "ab"


@pytest.mark.parametrize(
    "body",
    [
        b"<html>maintenance</html>",
        b"",
        b"[1, 2]",
        b'"a string"',
        b"{}",
        _body(content=[]),
        _body(content="not a list"),
        _body(content=[{"type": "tool_use"}]),
        json.dumps(
            {"content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn"}
        ).encode(),
        _body(content=[{"type": "text", "text": 5}]),
        _body(usage={"input_tokens": "9"}),
    ],
)
def test_a_body_that_is_not_json_or_has_the_wrong_shape_is_refused_as_parse_refused(
    body: bytes,
) -> None:
    with pytest.raises(ParseRefused) as caught:
        completion_from_response(body)
    assert isinstance(caught.value, VolabError)
    assert not isinstance(caught.value, ValueError)  # R30


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence"])
def test_a_reply_that_ended_on_its_own_is_accepted(stop_reason: str) -> None:
    assert completion_from_response(_body(stop_reason=stop_reason)).text == "hello"


def test_a_reply_cut_off_at_the_cap_is_refused_even_though_it_holds_text() -> None:
    # The first real-store sprint stored a hypothesis, critique and report that each stopped
    # mid-sentence, because this reply was accepted (R226).
    content = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": "a"},
    ]
    with pytest.raises(ParseRefused, match="max_tokens") as caught:
        completion_from_response(_body(content=content, stop_reason="max_tokens"))
    assert "thinking" in str(caught.value) and "text" in str(caught.value)


def test_a_reply_cut_off_inside_thinking_names_the_stop_reason_not_only_the_missing_text() -> None:
    # The second real-store sprint died on "holds no text block", which said nothing of why.
    content = [{"type": "thinking", "thinking": "", "signature": "s"}]
    with pytest.raises(ParseRefused, match="max_tokens"):
        completion_from_response(_body(content=content, stop_reason="max_tokens"))


def test_a_safety_refusal_is_refused_and_names_its_category() -> None:
    body = _body(content=[], stop_reason="refusal", stop_details={"category": "general_harms"})
    with pytest.raises(ParseRefused, match="refusal.*general_harms"):
        completion_from_response(body)


@pytest.mark.parametrize("stop_reason", [None, "pause_turn", "tool_use", 5])
def test_a_reply_with_no_or_another_stop_reason_is_refused(stop_reason: object) -> None:
    payload = json.loads(_body())
    if stop_reason is None:
        del payload["stop_reason"]
    else:
        payload["stop_reason"] = stop_reason
    with pytest.raises(ParseRefused, match="stop_reason"):
        completion_from_response(json.dumps(payload).encode())


def test_a_boolean_token_count_is_refused_rather_than_read_as_one() -> None:
    with pytest.raises(ParseRefused):
        completion_from_response(_body(usage={"input_tokens": True}))


def test_the_default_model_is_haiku_5_5_and_is_defined_in_one_place() -> None:
    assert DEFAULT_MODEL == "claude-haiku-5-5"
    source = _llm.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        assert handle.read().count('"claude-haiku-5-5"') == 1


def test_the_environment_completer_refuses_with_no_key_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOLAB_LLM_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="VOLAB_LLM_API_KEY"):
        completer_from_environment()


def test_the_environment_completer_refuses_an_empty_key_as_it_does_an_absent_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOLAB_LLM_API_KEY", "   ")
    with pytest.raises(ConfigError, match="VOLAB_LLM_API_KEY"):
        completer_from_environment()


def test_a_present_key_builds_a_callable_without_making_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the network was touched while building a completer")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setenv("VOLAB_LLM_API_KEY", "test-value")
    assert callable(completer_from_environment())


def test_importing_the_module_reads_no_environment_variable() -> None:
    import ast

    with open(_llm.__file__, encoding="utf-8") as handle:  # type: ignore[arg-type]
        tree = ast.parse(handle.read())
    for node in tree.body:  # module level only: function bodies run on call, not on import
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the module docstring talks about the environment; it does not read it
        assert "environ" not in ast.dump(node), ast.dump(node)


def test_the_fake_completer_hands_back_queued_texts_in_order_and_records_prompts() -> None:
    fake = FakeCompleter(["one", "two"])
    first, second = Prompt("sys", "u1"), Prompt("sys", "u2", max_tokens=5)
    assert fake(first).text == "one"
    assert fake(second).text == "two"
    assert fake.prompts == (first, second)


def test_the_fake_completer_reports_no_token_counts_rather_than_zero() -> None:
    result = FakeCompleter(["x"])(Prompt("s", "u"))
    assert result.input_tokens is None and result.output_tokens is None


def test_the_fake_completer_refuses_a_call_beyond_its_script_and_records_nothing_for_it() -> None:
    fake = FakeCompleter(["only"])
    fake(Prompt("s", "a"))
    with pytest.raises(RuntimeError, match="exhausted"):
        fake(Prompt("s", "b"))
    assert len(fake.prompts) == 1


def test_a_prompt_defaults_to_8192_max_tokens_and_is_frozen() -> None:
    # Haiku 5.5 thinks by default and thinking counts toward the cap. At 1024, every role but the
    # Configurator stopped mid-reply; the largest whole reply measured was 2057 tokens (R226).
    prompt = Prompt("s", "u")
    assert prompt.max_tokens == 8192
    with pytest.raises(AttributeError):
        prompt.user = "other"  # type: ignore[misc]
