"""Unit tests for the shared domain vocabulary. No database needed."""

import re
from enum import StrEnum

import pytest

from sr_db import domain
from sr_db.domain import ID_PREFIX, FailureCode, table_for_id

ALL_ENUMS: list[type[StrEnum]] = [
    obj
    for obj in vars(domain).values()
    if isinstance(obj, type) and issubclass(obj, StrEnum) and obj is not StrEnum
]


def test_domain_defines_enums() -> None:
    assert len(ALL_ENUMS) >= 20


@pytest.mark.parametrize("enum", ALL_ENUMS, ids=lambda e: e.__name__)
def test_enum_values_are_upper_snake_and_equal_their_names(enum: type[StrEnum]) -> None:
    for member in enum:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", member.value)
        assert member.value == member.name


def test_failure_codes_are_f00_to_f11_in_order() -> None:
    numbers = [code.value[:3] for code in FailureCode]

    assert numbers == [f"F{n:02d}" for n in range(12)]


def test_every_table_has_a_unique_id_prefix() -> None:
    assert len(ID_PREFIX) == 20
    assert len(set(ID_PREFIX.values())) == len(ID_PREFIX)
    assert ID_PREFIX["composer.referral"] == "GR-"
    assert ID_PREFIX["hub.hub_referral"] == "HR-"


@pytest.mark.parametrize(
    ("record_id", "table"),
    [
        ("GR-10234", "composer.referral"),
        ("HR-55210", "hub.hub_referral"),
        ("dl_8812", "hub.delivery"),
        ("da_1", "hub.delivery_attempt"),
        ("pt_42", "composer.patient_snapshot"),
    ],
)
def test_table_for_id_resolves_known_prefixes(record_id: str, table: str) -> None:
    assert table_for_id(record_id) == table


@pytest.mark.parametrize("record_id", ["", "dl_", "dl_abc", "XX-1", "GR10234", "dl_12 "])
def test_table_for_id_returns_none_for_unknown_or_malformed_ids(record_id: str) -> None:
    assert table_for_id(record_id) is None
