"""Cost estimates from token usage."""

import pytest

from sr_agent.model import TokenUsage
from sr_agent.pricing import PRICES, canonical_model, cost_usd


def test_each_kind_of_token_has_its_own_price() -> None:
    usage = TokenUsage(
        input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    price = PRICES["claude-opus-5-5"]
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(
        price.input + price.cache_write + price.cache_read + price.output
    )
    assert (price.input, price.output, price.cache_read) == (4.0, 20.0, 0.20)


def test_a_typical_diagnosis_costs_cents() -> None:
    usage = TokenUsage(
        input_tokens=2_000,
        cache_creation_input_tokens=15_000,
        cache_read_input_tokens=60_000,
        output_tokens=6_000,
    )
    # 0.008 + 0.075 + 0.012 + 0.12
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(0.215)


def test_an_unknown_model_has_no_estimate() -> None:
    assert cost_usd("fake-model", TokenUsage(input_tokens=10)) is None


def test_a_dated_snapshot_id_is_priced_as_its_alias() -> None:
    # A reply names the model that served it: an alias can come back as its dated snapshot.
    usage = TokenUsage(input_tokens=1_000_000)
    assert cost_usd("claude-haiku-4-5-20251001", usage) == cost_usd("claude-haiku-4-5", usage)
    assert canonical_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert canonical_model("claude-opus-5-5") == "claude-opus-5-5"
    assert canonical_model("claude-opus-5-5-2") == "claude-opus-5-5-2"  # not a date
