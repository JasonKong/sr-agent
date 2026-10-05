"""AnthropicModel through the real SDK over an httpx2 MockTransport (no network, no key)."""

import asyncio
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from sr_agent.anthropic_model import FALLBACK_BETA, INTERLEAVED_THINKING_BETA, AnthropicModel
from sr_agent.model import Effort, ModelReply, ModelRequest, ModelUnavailable, TokenUsage

TOOLS = [
    {
        "name": "get_referral",
        "description": "The linked view of a referral.",
        "input_schema": {"type": "object", "properties": {"referral_id": {"type": "string"}}},
    }
]
SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}
REQUEST = ModelRequest(
    system="You are SR-Agent.",
    tools=TOOLS,
    messages=[{"role": "user", "content": "Why?"}],
    max_tokens=16000,
    output_schema=SCHEMA,
)
REPLY_BODY: dict[str, Any] = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig-abc"},
        {"type": "text", "text": "Checking."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "get_referral",
            "input": {"referral_id": "GR-1"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 12,
        "output_tokens": 34,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": 3000,
    },
}

Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    def __init__(self, respond: Handler | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self._respond = respond or (lambda request: httpx2.Response(200, json=REPLY_BODY))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def body(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[-1].content)
        return data


def model_with(
    recorder: Recorder,
    *,
    model: str = "claude-opus-5-5",
    effort: Effort | None = "medium",
    fallback: bool = True,
    thinking_display: str = "omitted",
) -> AnthropicModel:
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return AnthropicModel(
        model=model,
        effort=effort,
        fallback=fallback,
        thinking_display=thinking_display,  # type: ignore[arg-type]
        client=client,
    )


def betas(recorder: Recorder) -> list[str]:
    header = recorder.requests[-1].headers.get("anthropic-beta", "")
    return [b.strip() for b in header.split(",") if b.strip()]


def complete(model: AnthropicModel) -> ModelReply:
    return asyncio.run(model.complete(REQUEST))


def test_the_request_carries_the_model_settings_and_caching() -> None:
    recorder = Recorder()
    complete(model_with(recorder))

    request = recorder.requests[0]
    assert request.url.path == "/v1/messages"
    body = recorder.body
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 16000
    # A breakpoint after the system prompt caches the tools and the system prompt across
    # cases; the top-level one caches each case's own history.
    assert body["system"] == [
        {"type": "text", "text": "You are SR-Agent.", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["tools"] == TOOLS
    assert body["messages"] == REQUEST.messages
    assert body["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body  # always on for Opus 5.5; effort is the control
    assert "tool_choice" not in body  # forced tool use is rejected; auto is the default


def test_sonnet_gets_the_same_shape_with_its_effort_sent_explicitly() -> None:
    recorder = Recorder()
    complete(model_with(recorder, model="claude-sonnet-5-5", effort="medium"))

    body = recorder.body
    assert body["model"] == "claude-sonnet-5-5"
    assert body["output_config"]["effort"] == "medium"  # the API default would be high
    assert "thinking" not in body  # adaptive by default; `disabled` is rejected
    assert body["fallbacks"] == "default"
    assert betas(recorder) == [FALLBACK_BETA]


def test_haiku_gets_a_thinking_budget_and_no_effort_or_fallbacks() -> None:
    recorder = Recorder()
    complete(model_with(recorder, model="claude-haiku-4-5", effort=None))

    body = recorder.body
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]
    assert "fallbacks" not in body
    # Thinking between tool calls needs the interleaved-thinking beta on Haiku 4.5.
    assert betas(recorder) == [INTERLEAVED_THINKING_BETA]
    assert body["cache_control"] == {"type": "ephemeral"}


def test_summarized_thinking_is_asked_for_only_where_it_exists() -> None:
    opus = Recorder()
    complete(model_with(opus, thinking_display="summarized"))
    assert opus.body["thinking"] == {"type": "adaptive", "display": "summarized"}

    haiku = Recorder()
    model = model_with(haiku, model="claude-haiku-4-5", effort=None, thinking_display="summarized")
    complete(model)
    assert haiku.body["thinking"] == {"type": "enabled", "budget_tokens": 4096}


def test_effort_must_match_what_the_model_takes() -> None:
    with pytest.raises(ModelUnavailable, match="no effort levels"):
        model_with(Recorder(), model="claude-haiku-4-5", effort="low")
    with pytest.raises(ModelUnavailable, match="needs an effort level"):
        model_with(Recorder(), model="claude-opus-5-5", effort=None)


def test_a_model_without_a_profile_is_refused_before_any_request() -> None:
    recorder = Recorder()
    with pytest.raises(ModelUnavailable, match="no request profile"):
        model_with(recorder, model="claude-opus-9")
    assert recorder.requests == []


def test_the_wrap_up_turn_forbids_tool_calls() -> None:
    recorder = Recorder()
    model = model_with(recorder)
    asyncio.run(model.complete(dataclasses.replace(REQUEST, tool_choice="none")))

    assert recorder.body["tool_choice"] == {"type": "none"}
    assert recorder.body["tools"] == TOOLS  # the tool list never changes during a run


def test_refusal_fallback_is_on_by_default_and_can_be_turned_off() -> None:
    with_fallback = Recorder()
    complete(model_with(with_fallback))
    assert with_fallback.body["fallbacks"] == "default"
    assert FALLBACK_BETA in with_fallback.requests[0].headers["anthropic-beta"]

    without = Recorder()
    model = model_with(without, fallback=False)
    complete(model)
    assert "fallbacks" not in without.body
    assert FALLBACK_BETA not in without.requests[0].headers.get("anthropic-beta", "")
    assert model.fallback is False


def test_the_reply_keeps_every_block_for_replay() -> None:
    reply = complete(model_with(Recorder()))

    assert reply.content == REPLY_BODY["content"]
    assert reply.stop_reason == "tool_use"
    assert reply.model == "claude-opus-5-5"
    assert reply.usage == TokenUsage(
        input_tokens=12, output_tokens=34, cache_read_input_tokens=3000
    )


def _error(status: int, kind: str, message: str) -> Handler:
    body = {"type": "error", "error": {"type": kind, "message": message}}
    return lambda request: httpx2.Response(status, json=body)


def _no_connection(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    ("respond", "expected"),
    [
        (_error(401, "authentication_error", "invalid x-api-key"), "ANTHROPIC_API_KEY"),
        (_error(404, "not_found_error", "model: nope"), "claude-opus-5-5"),
        (_error(429, "rate_limit_error", "slow down"), "rate limit"),
        (_error(529, "overloaded_error", "overloaded"), "529"),
        (_no_connection, "cannot reach the model API"),
    ],
)
def test_api_failures_become_model_unavailable(respond: Handler, expected: str) -> None:
    with pytest.raises(ModelUnavailable, match=expected):
        complete(model_with(Recorder(respond)))


def test_the_api_key_is_passed_to_the_sdk_when_given() -> None:
    model = AnthropicModel(
        model="claude-sonnet-5-5", effort="low", fallback=False, api_key="sk-test-123"
    )
    assert model.client.api_key == "sk-test-123"
    assert model.name == "claude-sonnet-5-5"


def no_credentials(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """No key, token or profile anywhere the SDK looks."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))


def test_missing_credentials_are_reported_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_credentials(monkeypatch, tmp_path)
    with pytest.raises(ModelUnavailable, match="ANTHROPIC_API_KEY"):
        AnthropicModel(model="claude-opus-5-5", effort="low", fallback=False)
