"""What each model accepts, so that every request fits its model (DESIGN.md section 7.4).

A parameter a model does not take is a 400, not a silent no-op, and the models differ:

- **Claude Opus 5.5 and Claude Sonnet 5.5** always think (adaptive thinking: `disabled` and
  a token budget are both rejected), take `output_config.effort`, and accept server-side
  refusal fallbacks (`fallbacks: "default"`). Their effort defaults differ (medium on Opus
  5.5, high on Sonnet 5.5), so effort is always sent.
- **Claude Haiku 4.5** rejects `effort`. It thinks only with a token budget
  (`{"type": "enabled", "budget_tokens": n}`), and thinking between tool calls needs the
  interleaved-thinking beta. It runs no safety classifier of the kind a fallback retries,
  so no fallback is sent.

A model without a profile is refused before any request is made, rather than sent
parameters it may reject. Adding a model means adding its profile and its price
(`sr_agent.pricing`); a test checks that every profile has one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sr_agent.model import ModelUnavailable


@dataclass(frozen=True)
class ModelProfile:
    effort: bool  # accepts output_config.effort
    thinking: Literal["adaptive", "budget"]  # always-on adaptive, or a token budget
    fallbacks: bool  # accepts server-side refusal fallbacks
    thinking_budget: int = 0  # tokens per reply, for "budget" thinking (at least 1,024)


PROFILES: dict[str, ModelProfile] = {
    "claude-opus-5-5": ModelProfile(effort=True, thinking="adaptive", fallbacks=True),
    "claude-sonnet-5-5": ModelProfile(effort=True, thinking="adaptive", fallbacks=True),
    "claude-haiku-4-5": ModelProfile(
        effort=False, thinking="budget", fallbacks=False, thinking_budget=4096
    ),
}


def profile_for(model: str) -> ModelProfile:
    """The model's profile. Raises `ModelUnavailable` for a model without one."""
    try:
        return PROFILES[model]
    except KeyError:
        known = ", ".join(PROFILES)
        raise ModelUnavailable(
            f"no request profile for model {model!r}: choose one of {known}, "
            "or add it to sr_agent.model_profiles.PROFILES"
        ) from None
