"""Scenario specifications: one YAML file per failure code (DESIGN.md sections 3.4 and 7.1).

YAML says *what* a scenario is: its leg, how CS hears about it, the root cause, the next step,
which outcome to inject with which parameters, the statuses to expect, and the evidence the
agent should cite. `faults.py` says *how* the outcome happens. Loading validates everything
against the code: outcome names, legs, parameters, tables and column names.
"""

from __future__ import annotations

import dataclasses
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sr_db.domain import FailureCode, HubReferralStatus, ReferralStatus
from sr_simulator.faults import OUTCOME_LEGS, OUTCOME_PARAMS, Injection, Leg, Params
from sr_simulator.rows import ROW_TYPES

SCENARIO_DIR = Path(__file__).parent / "scenarios"
DISTRACTOR_DIR = Path(__file__).parent / "distractor_specs"

Scalar = str | int | bool | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceSpec(_Strict):
    """Rows linked to the case in `table` that match every `where` entry must exist.

    A `where` value can be a list, meaning any of those values.
    """

    table: str
    where: dict[str, Scalar | list[Scalar]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_table_and_columns(self) -> EvidenceSpec:
        row_type = ROW_TYPES.get(self.table)
        if row_type is None:
            raise ValueError(f"unknown table {self.table!r}")
        columns = {f.name for f in dataclasses.fields(row_type)}
        unknown = set(self.where) - columns
        if unknown:
            raise ValueError(f"{self.table} has no column(s) {sorted(unknown)}")
        return self


class ExpectSpec(_Strict):
    composer_status: list[ReferralStatus] = Field(min_length=1)
    hub: Literal["present", "absent", "any"]
    hub_status: list[HubReferralStatus] = Field(default_factory=list)  # empty means any


class InjectSpec(_Strict):
    outcome: str | None = None
    params: dict[str, object] = Field(default_factory=dict)


class ScenarioSpec(_Strict):
    code: FailureCode
    leg: Leg
    title: str
    cs_hears: str
    root_cause: str
    recommended_action: str
    escalate_to_engineering: bool
    inject: InjectSpec
    expect: ExpectSpec
    evidence: list[EvidenceSpec]

    @model_validator(mode="after")
    def _outcome_matches_code(self) -> ScenarioSpec:
        outcome = self.inject.outcome
        if outcome is None:
            if self.code is not FailureCode.F00_NO_FAULT:
                raise ValueError(f"{self.code} must name an outcome to inject")
            return self
        if outcome not in OUTCOME_LEGS:
            raise ValueError(f"unknown outcome {outcome!r}; known: {sorted(OUTCOME_LEGS)}")
        if OUTCOME_LEGS[outcome] != self.leg:
            raise ValueError(f"outcome {outcome!r} belongs to leg {OUTCOME_LEGS[outcome]!r}")
        self.params()  # validate now, not at injection time
        return self

    def params(self) -> Params:
        assert self.inject.outcome is not None
        return OUTCOME_PARAMS[self.inject.outcome].model_validate(self.inject.params)

    def injection(self) -> Injection | None:
        if self.inject.outcome is None:
            return None
        return Injection(self.code, self.inject.outcome, self.params())


def parse_scenario(data: object) -> ScenarioSpec:
    return ScenarioSpec.model_validate(data)


@cache
def load_scenarios(directory: Path = SCENARIO_DIR) -> dict[FailureCode, ScenarioSpec]:
    """Every scenario, by code. Each file must be named after its code: `F07_….yaml`."""
    specs: dict[FailureCode, ScenarioSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = parse_scenario(yaml.safe_load(path.read_text()))
        if path.stem != spec.code.value:
            raise ValueError(f"{path.name} holds {spec.code}; name the file {spec.code}.yaml")
        specs[spec.code] = spec
    missing = set(FailureCode) - set(specs)
    if missing:
        raise ValueError(f"missing scenario files for {sorted(missing)}")
    return specs


def injections(specs: dict[FailureCode, ScenarioSpec]) -> dict[FailureCode, Injection]:
    return {code: inj for code, spec in specs.items() if (inj := spec.injection()) is not None}


class DistractorSpec(_Strict):
    """A distractor kind: what CS hears, the trap, and the evidence that shows the truth."""

    kind: str
    true_code: FailureCode | Literal["inherit"]  # "inherit": keeps the case's own label
    title: str
    cs_hears: str
    trap: str
    params: dict[str, object] = Field(default_factory=dict)
    evidence: list[EvidenceSpec]

    @model_validator(mode="after")
    def _known_kind(self) -> DistractorSpec:
        from sr_simulator.distractors import KINDS

        if self.kind not in KINDS:
            raise ValueError(f"unknown distractor kind {self.kind!r}; known: {sorted(KINDS)}")
        if (self.true_code == "inherit") != KINDS[self.kind].any_label:
            raise ValueError(f"{self.kind}: true_code 'inherit' is only for kinds on any label")
        self.validated_params()
        return self

    def validated_params(self) -> Params:
        from sr_simulator.distractors import KINDS

        return KINDS[self.kind].params.model_validate(self.params)


def parse_distractor(data: object) -> DistractorSpec:
    return DistractorSpec.model_validate(data)


@cache
def load_distractors(directory: Path = DISTRACTOR_DIR) -> dict[str, DistractorSpec]:
    """Every distractor kind, by name. Each file must be named after its kind."""
    from sr_simulator.distractors import KINDS

    specs: dict[str, DistractorSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = parse_distractor(yaml.safe_load(path.read_text()))
        if path.stem != spec.kind:
            raise ValueError(f"{path.name} holds {spec.kind}; name the file {spec.kind}.yaml")
        specs[spec.kind] = spec
    missing = set(KINDS) - set(specs)
    if missing:
        raise ValueError(f"missing distractor files for {sorted(missing)}")
    return specs
