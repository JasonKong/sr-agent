"""The model seam: what the loop needs from a language model (DESIGN.md section 7.4).

The conversation is kept in the Anthropic Messages format, which both planned providers
(the Anthropic API and Amazon Bedrock) speak. A reply's content blocks are replayed
verbatim on the next request: thinking blocks must come back unchanged, so a
provider-neutral format that dropped them would break the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

Block = dict[str, Any]
"""One content block in the Messages format: text, thinking, tool_use, tool_result, …"""

Message = dict[str, Any]
"""One message: {"role": "user" | "assistant", "content": str | list[Block]}."""

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelUnavailable(Exception):  # noqa: N818 - reads naturally at raise sites
    """The model API could not answer: credentials, rate limits, outages, the network."""


class TokenUsage(BaseModel):
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        """Every token processed: input (cached or not) and output."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
            + self.output_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class ModelRequest:
    system: str
    tools: list[dict[str, Any]]
    messages: list[Message]
    max_tokens: int


class ToolUse(BaseModel):
    """A tool call the model asked for. Model output is untrusted, so it is validated."""

    id: str
    name: str
    input: dict[str, Any]


class ModelReply(BaseModel):
    content: list[Block]
    stop_reason: str | None
    usage: TokenUsage
    model: str  # the model that served this reply, which a fallback can change

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_uses(self) -> list[ToolUse]:
        """The tool calls, in order. Raises ValueError if one is malformed."""
        try:
            return [ToolUse.model_validate(b) for b in self.content if b.get("type") == "tool_use"]
        except ValidationError as error:
            raise ValueError(f"malformed tool_use block: {error}") from error


class ModelClient(Protocol):
    @property
    def name(self) -> str:
        """The model id requested, for the run record."""
        ...

    async def complete(self, request: ModelRequest) -> ModelReply: ...
