"""`ModelClient` over the Anthropic Messages API (DESIGN.md section 7.4).

Each request is built from the model's profile (`sr_agent.model_profiles`), because a
parameter a model does not take is a 400:
- Thinking: Opus 5.5 and Sonnet 5.5 always think, and `effort` is the control. Effort is
  always sent, since the defaults differ (medium on Opus 5.5, high on Sonnet 5.5).
  `thinking_display="summarized"` asks for readable summaries of the thinking, at the same
  cost. Haiku 4.5 takes no effort; it thinks with a token budget, between tool calls too
  (the interleaved-thinking beta).
- `tool_choice` stays at its default, `auto`, since forcing a tool call is rejected; the
  wrap-up turn after a limit sends `none`.
- The answer schema goes in `output_config.format` (structured outputs) on every request,
  so it is part of the stable, cached request.
- Two cache breakpoints. One after the system prompt caches the tool definitions and the
  system prompt, which every case of an eval run shares. The automatic one (`cache_control`
  at the top level) caches each conversation's history as it grows.
- Server-side refusal fallback is on by default where the model accepts it: if a safety
  classifier declines a request, the API retries it on another model in the same call.
  The reply's `model` says which model served it, and the run record keeps that.
"""

from __future__ import annotations

from typing import Any, Literal

import anthropic
from anthropic import AsyncAnthropic

from sr_agent.model import Effort, ModelReply, ModelRequest, ModelUnavailable, TokenUsage
from sr_agent.model_profiles import profile_for
from sr_agent.settings import AgentSettings

FALLBACK_BETA = "server-side-fallback-2026-07-01"
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"

ThinkingDisplay = Literal["omitted", "summarized"]


class AnthropicModel:
    def __init__(
        self,
        *,
        model: str,
        effort: Effort | None,
        fallback: bool,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        thinking_display: ThinkingDisplay = "omitted",
        client: AsyncAnthropic | None = None,
    ) -> None:
        """Without `api_key`, the SDK finds credentials itself (environment or profile).

        Raises `ModelUnavailable` at once for a model without a profile, an effort the
        model does not take, or no credentials: the SDK would otherwise fail only when the
        first request is built, after the run has started.
        """
        profile = profile_for(model)
        if profile.effort and effort is None:
            raise ModelUnavailable(f"{model} needs an effort level")
        if not profile.effort and effort is not None:
            raise ModelUnavailable(
                f"{model} has no effort levels: leave out --effort and SRA_EFFORT"
            )
        self._model = model
        self._profile = profile
        self._effort = effort
        self._fallback = fallback and profile.fallbacks
        self._thinking_display: ThinkingDisplay = thinking_display
        self.client = client or AsyncAnthropic(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries
        )
        c = self.client
        if c.api_key is None and c.auth_token is None and c.credentials is None:
            raise ModelUnavailable(
                "no model API credentials: set ANTHROPIC_API_KEY in .env or the environment"
            )

    @property
    def name(self) -> str:
        return self._model

    @property
    def effort(self) -> Effort | None:
        """The effort sent with every request; None for a model without effort levels."""
        return self._effort

    @property
    def fallback(self) -> bool:
        """Whether refusals are retried on another model (asked for, and accepted)."""
        return self._fallback

    @property
    def thinking(self) -> str:
        """How the model thinks, for run and report records."""
        if self._profile.thinking == "budget":
            return f"budget {self._profile.thinking_budget}"
        return "adaptive, summarized" if self._thinking_display == "summarized" else "adaptive"

    def request_params(self, request: ModelRequest) -> dict[str, Any]:
        """The keyword arguments of `client.beta.messages.create` for this request."""
        output_config: dict[str, Any] = {}
        if self._effort is not None:
            output_config["effort"] = self._effort
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
        betas: list[str] = []
        thinking: dict[str, Any] | None = None
        if self._profile.thinking == "budget":
            thinking = {"type": "enabled", "budget_tokens": self._profile.thinking_budget}
            betas.append(INTERLEAVED_THINKING_BETA)
        elif self._thinking_display == "summarized":
            thinking = {"type": "adaptive", "display": "summarized"}
        if self._fallback:
            betas.append(FALLBACK_BETA)
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "tools": request.tools,
            "messages": request.messages,
            "cache_control": {"type": "ephemeral"},
        }
        if output_config:
            params["output_config"] = output_config
        if thinking is not None:
            params["thinking"] = thinking
        if request.tool_choice == "none":
            params["tool_choice"] = {"type": "none"}
        if betas:
            params["betas"] = betas
        if self._fallback:
            params["fallbacks"] = "default"
        return params

    async def complete(self, request: ModelRequest) -> ModelReply:
        try:
            message = await self.client.beta.messages.create(**self.request_params(request))
        except anthropic.AuthenticationError as error:
            raise ModelUnavailable(
                "the model API refused the credentials (401): check ANTHROPIC_API_KEY"
            ) from error
        except anthropic.NotFoundError as error:
            raise ModelUnavailable(
                f"the model API does not know model {self._model!r} (404): check SRA_MODEL"
            ) from error
        except anthropic.RateLimitError as error:
            raise ModelUnavailable(
                "the model API rate limit was hit, even after retries (429)"
            ) from error
        except anthropic.APIStatusError as error:
            raise ModelUnavailable(
                f"the model API failed ({error.status_code}): {error.message}"
            ) from error
        except anthropic.APIConnectionError as error:
            raise ModelUnavailable(f"cannot reach the model API: {error}") from error

        data: dict[str, Any] = message.to_dict()
        usage = message.usage
        return ModelReply(
            content=data["content"],
            stop_reason=message.stop_reason,
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                output_tokens=usage.output_tokens,
            ),
            model=message.model,
        )


def model_from_settings(
    settings: AgentSettings,
    *,
    model: str | None = None,
    effort: Effort | None = None,
    timeout_s: float | None = None,
    thinking_display: ThinkingDisplay | None = None,
) -> AnthropicModel:
    """The model for `sra ask`, `sra serve` and `sr-evals run`: arguments over settings.

    A model without effort levels drops the default effort, but an effort asked for, by an
    argument or by SRA_EFFORT, is refused rather than silently ignored.
    """
    name = model or settings.model
    profile = profile_for(name)
    chosen: Effort | None
    if profile.effort:
        chosen = effort or settings.effort
    else:
        chosen = effort or (settings.effort if "effort" in settings.model_fields_set else None)
    key = settings.anthropic_api_key
    return AnthropicModel(
        model=name,
        effort=chosen,
        fallback=settings.fallback == "default",
        api_key=key.get_secret_value() if key else None,
        timeout_s=timeout_s or settings.time_limit_s,
        thinking_display=thinking_display or settings.thinking_display,
    )
