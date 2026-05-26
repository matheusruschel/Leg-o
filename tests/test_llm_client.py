from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest

from lego.llm.client import ClaudeClient, _parse_json_lenient


def _resp(text: str, input_tokens: int = 10, output_tokens: int = 5):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _make_sdk(side_effects):
    """Build a fake Anthropic SDK client whose messages.create returns/raises
    each item in side_effects in order."""
    messages = MagicMock()
    iterator = iter(side_effects)

    def _create(**_kwargs):
        item = next(iterator)
        if isinstance(item, Exception):
            raise item
        return item

    messages.create.side_effect = _create
    return SimpleNamespace(messages=messages)


def test_call_returns_text_and_records_usage():
    sdk = _make_sdk([_resp("hello", input_tokens=42, output_tokens=7)])
    client = ClaudeClient(api_key="x", sdk_client=sdk)

    out = client.call([{"role": "user", "content": "hi"}], call_type="analysis")

    assert out == "hello"
    rep = client.tracker.report()
    assert rep["total_input_tokens"] == 42
    assert rep["total_output_tokens"] == 7
    assert rep["by_type"]["analysis"]["calls"] == 1


def test_rate_limit_retries_with_backoff(monkeypatch):
    monkeypatch.setattr("lego.llm.client.time.sleep", lambda _s: None)
    rate_err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(rate_err, "rate limited")
    sdk = _make_sdk([rate_err, rate_err, _resp("ok")])

    client = ClaudeClient(api_key="x", sdk_client=sdk, rate_limit_attempts=3)
    assert client.call([{"role": "user", "content": "hi"}]) == "ok"
    assert sdk.messages.create.call_count == 3


def test_rate_limit_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr("lego.llm.client.time.sleep", lambda _s: None)
    rate_err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(rate_err, "rate limited")
    sdk = _make_sdk([rate_err, rate_err, rate_err])

    client = ClaudeClient(api_key="x", sdk_client=sdk, rate_limit_attempts=3)
    with pytest.raises(anthropic.RateLimitError):
        client.call([{"role": "user", "content": "hi"}])


def test_overloaded_retries_after_wait(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr("lego.llm.client.time.sleep", lambda s: waits.append(s))

    overload = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    Exception.__init__(overload, "overloaded_error: server overloaded")
    overload.status_code = 529

    sdk = _make_sdk([overload, _resp("ok")])
    client = ClaudeClient(api_key="x", sdk_client=sdk, overload_wait_seconds=30.0)

    assert client.call([{"role": "user", "content": "hi"}]) == "ok"
    assert 30.0 in waits


def test_call_json_parses_clean_json():
    sdk = _make_sdk([_resp('{"a": 1}')])
    client = ClaudeClient(api_key="x", sdk_client=sdk)
    assert client.call_json([{"role": "user", "content": "hi"}]) == {"a": 1}


def test_call_json_strips_markdown_fences():
    fenced = "Here you go:\n```json\n{\"a\": 1, \"b\": [1,2]}\n```\n"
    sdk = _make_sdk([_resp(fenced)])
    client = ClaudeClient(api_key="x", sdk_client=sdk)
    assert client.call_json([{"role": "user", "content": "hi"}]) == {"a": 1, "b": [1, 2]}


def test_call_json_strips_preamble():
    raw = 'Sure! Here is the JSON you requested: {"x": "y"}'
    sdk = _make_sdk([_resp(raw)])
    client = ClaudeClient(api_key="x", sdk_client=sdk)
    assert client.call_json([{"role": "user", "content": "hi"}]) == {"x": "y"}


def test_call_json_makes_fix_call_when_parse_fails():
    sdk = _make_sdk([_resp("this is not json at all"), _resp('{"fixed": true}')])
    client = ClaudeClient(api_key="x", sdk_client=sdk)

    result = client.call_json([{"role": "user", "content": "hi"}])
    assert result == {"fixed": True}
    assert sdk.messages.create.call_count == 2
    rep = client.tracker.report()
    assert rep["by_type"]["fix"]["calls"] == 1


def test_call_dry_run_returns_none_and_no_api_call():
    sdk = _make_sdk([])  # no responses prepared; should not be invoked
    client = ClaudeClient(api_key="x", sdk_client=sdk)
    assert client.call_dry_run([{"role": "user", "content": "hi"}]) is None
    sdk.messages.create.assert_not_called()


def test_parse_json_lenient_handles_truncated_with_extraction():
    # Trailing prose after the JSON object should still parse via brace extraction.
    text = '{"a": 1, "b": 2}\n\nThanks!'
    assert _parse_json_lenient(text) == {"a": 1, "b": 2}
