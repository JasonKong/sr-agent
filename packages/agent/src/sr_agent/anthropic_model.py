"""`ModelClient` over the Anthropic Messages API (DESIGN.md section 7.4).

Request choices, for Claude Opus 5.5 and its peers:
- Thinking is always on and is not configured here; `effort` is the control (the API
  default for Opus 5.5 is medium, so it is always sent explicitly).
- `tool_choice` stays at its default, `auto`, since forcing a tool call is rejected; the
  wrap-up turn after a limit sends `none`.
- The answer schema goes in `output_config.format` (structured outputs) on every request,
  so it is part of the stable, cached request.
- Automatic prompt caching (`cache_control` at the top level) caches the longest stable
  prefix of each request: the tool definitions, the system prompt and the history so far.
- Server-side refusal fallback is on by default: if a safety classifier declines a request,
  the API retries it on another model in the same call. The reply's `model` says which
  model served it, and the run record keeps that.
"""

from __future__ import annotations

from typing import Any, cast

import anthropic
from anthropic import AsyncAnthropic, omit
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaToolUnionParam

from sr_agent.model import Effort, ModelReply, ModelRequest, ModelUnavailable, TokenUsage

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicModel:
    def __init__(
        self,
        *,
        model: str,
        effort: Effort,
        fallback: bool,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        client: AsyncAnthropic | None = None,
    ) -> None:
        """Without `api_key`, the SDK finds credentials itself (environment or profile).

        Raises `ModelUnavailable` at once if it finds none: the SDK would otherwise fail
        only when the first request is built, after the run has started.
        """
        self._model = model
        self._effort: Effort = effort
        self._fallback = fallback
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

    def _output_config(self, request: ModelRequest) -> BetaOutputConfigParam:
        config: BetaOutputConfigParam = {"effort": self._effort}
        if request.output_schema is not None:
            config["format"] = {"type": "json_schema", "schema": request.output_schema}
        return config

    async def complete(self, request: ModelRequest) -> ModelReply:
        try:
            message = await self.client.beta.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=[{"type": "text", "text": request.system}],
                tools=cast("list[BetaToolUnionParam]", request.tools),
                messages=cast("list[BetaMessageParam]", request.messages),
                output_config=self._output_config(request),
                tool_choice={"type": "none"} if request.tool_choice == "none" else omit,
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA] if self._fallback else omit,
                fallbacks="default" if self._fallback else omit,
            )
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
