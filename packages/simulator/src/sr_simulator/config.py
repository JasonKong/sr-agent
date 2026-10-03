"""Simulator settings (DESIGN.md sections 3.6 and 7.1)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NZ = ZoneInfo("Pacific/Auckland")

DEFAULT_ANCHOR = datetime(2026, 10, 1, 9, 0, tzinfo=NZ)

# Upper bounds come from the fictional name lists and the four-digit synthetic NHI.
MAX_PRACTICES = 64
MAX_CLINICS = 160
MAX_REFERRALS = 9999


@dataclass(frozen=True)
class SimConfig:
    """Everything that determines the generated world. Same config, same data."""

    seed: int = 42
    anchor_time: datetime = DEFAULT_ANCHOR
    practices: int = 30
    clinics: int = 40
    referrals: int = 2000
    history_days: int = 90
    failure_rate: float = 0.15  # share of referrals given an injected failure (F01-F11)
    distractor_rate: float = 0.05  # share given a distractor (DESIGN.md section 3.5)

    def __post_init__(self) -> None:
        if not 0 <= self.distractor_rate <= 0.2:
            raise ValueError(
                f"distractor_rate must be between 0 and 0.2, got {self.distractor_rate}"
            )
        if not 0 <= self.failure_rate <= 0.5:
            raise ValueError(f"failure_rate must be between 0 and 0.5, got {self.failure_rate}")
        if self.anchor_time.tzinfo is None:
            raise ValueError("anchor_time must include a timezone, for example +13:00")
        for name, value, upper in [
            ("practices", self.practices, MAX_PRACTICES),
            ("clinics", self.clinics, MAX_CLINICS),
            ("referrals", self.referrals, MAX_REFERRALS),
            ("history_days", self.history_days, 3650),
        ]:
            if not 1 <= value <= upper:
                raise ValueError(f"{name} must be between 1 and {upper}, got {value}")

    @property
    def history_start(self) -> datetime:
        return self.anchor_time - timedelta(days=self.history_days)
