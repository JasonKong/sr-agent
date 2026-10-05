"""Model profiles: what each model accepts, and the model built from the settings."""

from pathlib import Path

import pytest

from sr_agent.anthropic_model import model_from_settings
from sr_agent.model import ModelUnavailable
from sr_agent.model_profiles import PROFILES, profile_for
from sr_agent.pricing import PRICES
from sr_agent.settings import AgentSettings


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No .env and no SRA_ variables from the developer's shell; a dummy key for the SDK."""
    monkeypatch.chdir(tmp_path)
    for name in ("SRA_MODEL", "SRA_EFFORT", "SRA_FALLBACK", "SRA_THINKING_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_the_three_candidate_models_have_profiles() -> None:
    opus, sonnet, haiku = (
        profile_for(m) for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5")
    )
    assert (opus.effort, opus.thinking, opus.fallbacks) == (True, "adaptive", True)
    assert (sonnet.effort, sonnet.thinking, sonnet.fallbacks) == (True, "adaptive", True)
    assert (haiku.effort, haiku.thinking, haiku.fallbacks) == (False, "budget", False)
    assert 1024 <= haiku.thinking_budget < 16_000  # the API minimum; below max_tokens


def test_a_model_without_a_profile_is_refused_by_name() -> None:
    with pytest.raises(ModelUnavailable, match=r"claude-opus-9.*claude-haiku-4-5"):
        profile_for("claude-opus-9")


def test_every_model_that_can_serve_a_turn_has_a_price() -> None:
    # The requested models, and the models a refusal fallback can hand a turn to.
    for model in [*PROFILES, "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5"]:
        assert model in PRICES, model


def test_the_default_model_is_opus_at_medium_with_fallback() -> None:
    model = model_from_settings(AgentSettings())
    assert model.name == "claude-opus-5-5"
    assert model.effort == "medium"
    assert model.fallback is True
    assert model.thinking == "adaptive"


def test_a_model_without_effort_levels_drops_the_default_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SRA_MODEL", "claude-haiku-4-5")
    model = model_from_settings(AgentSettings())
    assert model.effort is None
    assert model.fallback is False  # Haiku runs no classifier that a fallback retries
    assert model.thinking == "budget 4096"


@pytest.mark.parametrize("where", ["argument", "environment"])
def test_an_effort_asked_of_a_model_without_effort_levels_is_refused(
    where: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SRA_MODEL", "claude-haiku-4-5")
    if where == "environment":
        monkeypatch.setenv("SRA_EFFORT", "high")
    with pytest.raises(ModelUnavailable, match="claude-haiku-4-5 has no effort levels"):
        model_from_settings(AgentSettings(), effort="low" if where == "argument" else None)


def test_arguments_override_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRA_FALLBACK", "off")
    model = model_from_settings(
        AgentSettings(),
        model="claude-sonnet-5-5",
        effort="low",
        thinking_display="summarized",
    )
    assert (model.name, model.effort, model.fallback) == ("claude-sonnet-5-5", "low", False)
    assert model.thinking == "adaptive, summarized"


def test_an_unknown_model_fails_before_the_credentials_are_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ModelUnavailable, match="no request profile"):
        model_from_settings(AgentSettings(), model="claude-typo")
