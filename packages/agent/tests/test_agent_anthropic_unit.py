"""AnthropicModel through the real SDK over an httpx2 MockTransport (no network, no key)."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from sr_agent.anthropic_model import FALLBACK_BETA, AnthropicModel
from sr_agent.model import ModelReply, ModelRequest, ModelUnavailable, TokenUsage

TOOLS = [
    {
        "name": "get_referral",
        "description": "The linked view of a referral.",
        "input_schema": {"type": "object", "properties": {"referral_id": {"type": "string"}}},
    }
]
REQUEST = ModelRequest(
    system="You are SR-Agent.",
    tools=TOOLS,
    messages=[{"role": "user", "content": "Why?"}],
    max_tokens=16000,
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


def model_with(recorder: Recorder, *, fallback: bool = True) -> AnthropicModel:
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder)),
    )
    return AnthropicModel(
        model="claude-opus-5-5", effort="medium", fallback=fallback, client=client
    )


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
    assert body["system"] == [{"type": "text", "text": "You are SR-Agent."}]
    assert body["tools"] == TOOLS
    assert body["messages"] == REQUEST.messages
    assert body["output_config"] == {"effort": "medium"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body  # always on for Opus 5.5; effort is the control
    assert "tool_choice" not in body  # forced tool use is rejected; auto is the default


def test_refusal_fallback_is_on_by_default_and_can_be_turned_off() -> None:
    with_fallback = Recorder()
    complete(model_with(with_fallback))
    assert with_fallback.body["fallbacks"] == "default"
    assert FALLBACK_BETA in with_fallback.requests[0].headers["anthropic-beta"]

    without = Recorder()
    complete(model_with(without, fallback=False))
    assert "fallbacks" not in without.body
    assert FALLBACK_BETA not in without.requests[0].headers.get("anthropic-beta", "")


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
    model = AnthropicModel(model="m", effort="low", fallback=False, api_key="sk-test-123")
    assert model.client.api_key == "sk-test-123"
    assert model.name == "m"


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
        AnthropicModel(model="m", effort="low", fallback=False)
