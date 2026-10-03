"""Unit tests for simulator configuration and derived random streams."""

from datetime import UTC, datetime, timedelta

import pytest

from sr_simulator.config import DEFAULT_ANCHOR, SimConfig
from sr_simulator.rng import derive_seed, rng_for


class TestDeriveSeed:
    def test_is_stable(self) -> None:
        assert derive_seed(42, "referral", 7) == derive_seed(42, "referral", 7)

    def test_changes_with_any_part(self) -> None:
        base = derive_seed(42, "referral", 7)

        assert derive_seed(43, "referral", 7) != base
        assert derive_seed(42, "clinic", 7) != base
        assert derive_seed(42, "referral", 8) != base

    def test_parts_cannot_run_together(self) -> None:
        # ("ab", "c") and ("a", "bc") must not collide, nor "1:2" and (1, 2).
        assert derive_seed(1, "ab", "c") != derive_seed(1, "a", "bc")
        assert derive_seed(1, "1:2") != derive_seed(1, 1, 2)

    def test_streams_are_independent(self) -> None:
        first = rng_for(42, "referral", 1)
        untouched = rng_for(42, "referral", 2).random()
        for _ in range(100):
            first.random()

        assert rng_for(42, "referral", 2).random() == untouched


class TestSimConfig:
    def test_defaults_match_the_design(self) -> None:
        config = SimConfig()

        assert (config.seed, config.practices, config.clinics, config.referrals) == (
            42,
            30,
            40,
            2000,
        )
        assert config.anchor_time == DEFAULT_ANCHOR
        assert DEFAULT_ANCHOR.isoformat() == "2026-10-01T09:00:00+13:00"

    def test_anchor_time_must_be_timezone_aware(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            SimConfig(anchor_time=datetime(2026, 10, 1, 9, 0))

    def test_anchor_time_in_another_zone_is_accepted(self) -> None:
        utc = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)

        assert SimConfig(anchor_time=utc).anchor_time == utc

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"practices": 0},
            {"clinics": 0},
            {"referrals": 0},
            {"history_days": 0},
            {"practices": 65},
            {"clinics": 161},
            {"referrals": 10_000},
        ],
    )
    def test_rejects_out_of_range_sizes(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError, match="must be between"):
            SimConfig(**kwargs)  # type: ignore[arg-type]

    def test_history_starts_before_anchor(self) -> None:
        config = SimConfig(history_days=10)

        assert config.history_start == config.anchor_time - timedelta(days=10)
