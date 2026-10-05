"""Estimated model cost from token usage, in US dollars.

List prices per million tokens for the Claude API, as of September 2026. Cache writes are
priced for the default 5-minute cache (1.25 times input); cache reads are far cheaper than
input, which is why an append-only history matters. An unknown model has no estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

from sr_agent.model import TokenUsage


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20),
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_write=1.25, cache_read=0.10),
    "claude-opus-5": Price(input=5.00, output=25.00, cache_write=6.25, cache_read=0.50),
    "claude-fable-5-1": Price(input=10.00, output=50.00, cache_write=12.50, cache_read=0.25),
}


def cost_usd(model: str, usage: TokenUsage) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
        + usage.output_tokens * price.output
    ) / 1_000_000
