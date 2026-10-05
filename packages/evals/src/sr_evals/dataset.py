"""The evaluation dataset (DESIGN.md section 7.5), built from the simulator's ground truth.

`v1` holds 75 cases from the default seeded world: 5 clean cases for each of the 12 codes,
and 15 cases carrying a distractor. Each question names its referral one of three ways, as
CS would hear it: the Composer id, the Hub id, or only the practice, GP, clinic and day.
`v1` is the development set: the agent is tuned against it. `holdout_v1` is built the same
way from another seed (43), with its own selection and wording, and is the test set: it is
run once, at the end, on the configuration chosen on `v1` (section 7.5).

Each dataset is frozen to `packages/evals/datasets/<version>.json`, so runs are comparable.
A test rebuilds each from its world: if the simulator changes, the test fails and the
dataset is rebuilt on purpose (`sr-evals build --set <version>`), never silently.

Selection is deterministic (its own seeded random stream). Cases of one code are spread
over different clinics where possible: clinic-level faults cluster on each clinic's most
recent referrals, and five cases from one clinic would test the same thing five times.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from sr_db.domain import FailureCode
from sr_simulator.case_rows import expected_evidence
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import Referral, World
from sr_simulator.scenario import load_distractors, load_scenarios

DATASET_DIR = Path(__file__).resolve().parents[2] / "datasets"
DATASET_V1 = DATASET_DIR / "v1.json"
HOLDOUT_SEED = 43
DATASETS: dict[str, Path] = {"v1": DATASET_V1, "holdout_v1": DATASET_DIR / "holdout_v1.json"}
# The world each dataset is built from: the default world, or the holdout seed.
DATASET_SEEDS: dict[str, int] = {"v1": SimConfig().seed, "holdout_v1": HOLDOUT_SEED}

PER_CODE = 5
DISTRACTOR_PLAN: dict[str, int] = {
    "recovered_pms_auth": 3,
    "recovered_hub_send": 2,
    "delivered_before_expiry": 2,
    "in_progress": 3,
    "injection_in_note": 3,
    "misleading_ticket": 2,
}

Reference = Literal["composer_id", "hub_id", "clinic_and_date"]


class ExpectedRecord(BaseModel):
    table: str
    record_id: str
    source: Literal["scenario", "distractor"]


class EvalCase(BaseModel):
    case_id: str
    question: str
    reference: Reference
    composer_id: str
    hub_id: str | None
    code: FailureCode
    distractor: str | None
    expected_evidence: list[ExpectedRecord]
    escalate_to_engineering: bool


class Dataset(BaseModel):
    version: str
    world: dict[str, Any]  # the SimConfig that generates the world, as in labels.json
    cases: list[EvalCase]


def config_document(config: SimConfig) -> dict[str, Any]:
    return {
        "seed": config.seed,
        "anchor_time": config.anchor_time.isoformat(),
        "practices": config.practices,
        "clinics": config.clinics,
        "referrals": config.referrals,
        "history_days": config.history_days,
        "failure_rate": config.failure_rate,
        "distractor_rate": config.distractor_rate,
        "tickets": config.tickets,
    }


def config_from(document: Mapping[str, Any]) -> SimConfig:
    fields = dict(document)
    fields["anchor_time"] = datetime.fromisoformat(fields["anchor_time"])
    return SimConfig(**fields)


def world_for(dataset: Dataset) -> World:
    return generate(config_from(dataset.world))


def load_dataset(path: Path) -> Dataset:
    return Dataset.model_validate_json(path.read_text())


def write_dataset(dataset: Dataset, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataset.model_dump(mode="json"), indent=2) + "\n")


_OPENERS = ["", "Hi team, ", "Quick one. ", "From a phone call: ", "Escalated by the front desk: "]
_ASKS = [
    "Can you find out why?",
    "What happened?",
    "Can you check?",
    "Any idea what went wrong?",
    "Could you look into it?",
]
# When what CS heard is itself a question ("Did the specialist get it?"), the ask must not
# presuppose a fault: F00 cases test whether the agent invents one.
_NEUTRAL_ASKS = ["Can you check?", "Could you look into it?", "Can you confirm?"]
_CLINIC_SIDE = {"deliver", "respond"}  # legs where the clinic usually calls


class _Words:
    """Question wording, from its own random stream so it never shifts case selection."""

    def __init__(self, world: World, config: SimConfig, version: str) -> None:
        self._rng = random.Random(f"sr-evals-{version}-wording")
        self._tz = config.anchor_time.tzinfo
        self._world = world
        self._practices = {p.id: p for p in world.gp_practices}
        self._gps = {u.id: u for u in world.gp_users}
        self._clinics = {c.id: c for c in world.clinics}

    def _day(self, referral: Referral) -> datetime:
        return referral.created_at.astimezone(self._tz)

    def _unique_by_clinic_and_day(self, referral: Referral) -> bool:
        day = self._day(referral).date()
        similar = [
            r
            for r in self._world.referrals
            if r.practice_id == referral.practice_id
            and r.recipient_clinic_ref == referral.recipient_clinic_ref
            and abs(self._day(r).date() - day) <= timedelta(days=1)
        ]
        return len(similar) == 1

    def reference(self, referral: Referral) -> Reference:
        draw = self._rng.random()
        if draw >= 0.8 and self._unique_by_clinic_and_day(referral):
            return "clinic_and_date"
        if draw >= 0.5 and referral.hub_referral_id:
            return "hub_id"
        return "composer_id"

    def question(self, referral: Referral, reference: Reference, leg: str, cs_hears: str) -> str:
        practice = self._practices[referral.practice_id].name
        clinic = self._clinics[referral.recipient_clinic_ref].name
        gp = self._gps[referral.gp_user_id].name
        if reference == "composer_id":
            ref = self._rng.choice(
                [f"referral {referral.id}", referral.id, f"the referral {referral.id}"]
            )
        elif reference == "hub_id":
            ref = self._rng.choice(
                [
                    f"Hub referral {referral.hub_referral_id}",
                    f"{referral.hub_referral_id} in the Hub",
                ]
            )
        else:
            day = self._day(referral)
            ref = f"the referral {gp} at {practice} sent to {clinic} on {day.day} {day:%B}"
        caller = clinic if leg in _CLINIC_SIDE else practice
        opener = self._rng.choice(_OPENERS)
        ask = self._rng.choice(_NEUTRAL_ASKS if cs_hears.endswith("?") else _ASKS)
        form = self._rng.randrange(3)
        if form == 0:
            return f'{opener}{caller} called about {ref}. They say: "{cs_hears}" {ask}'
        if form == 1:
            return f"{opener}About {ref}: {cs_hears} {ask}"
        return f"{opener}{cs_hears} This is {ref}, from {caller}. {ask}"


def _spread(
    candidates: Sequence[Referral], n: int, rng: random.Random, what: str
) -> list[Referral]:
    """`n` candidates, over as many different clinics as possible."""
    pool = sorted(candidates, key=lambda r: r.id)
    rng.shuffle(pool)
    chosen: list[Referral] = []
    clinics: set[str] = set()
    for referral in pool:
        if len(chosen) < n and referral.recipient_clinic_ref not in clinics:
            chosen.append(referral)
            clinics.add(referral.recipient_clinic_ref)
    for referral in pool:
        if len(chosen) < n and referral not in chosen:
            chosen.append(referral)
    if len(chosen) < n:
        raise ValueError(f"only {len(chosen)} {what} cases exist; {n} needed")
    return sorted(chosen, key=lambda r: int(r.id.split("-")[1]))


def build_dataset(
    world: World,
    config: SimConfig,
    *,
    per_code: int = PER_CODE,
    distractor_plan: Mapping[str, int] = DISTRACTOR_PLAN,
    version: str = "v1",
) -> Dataset:
    rng = random.Random(f"sr-evals-{version}-selection")
    words = _Words(world, config, version)
    scenarios = load_scenarios()
    distractors = load_distractors()

    def true_code(referral: Referral) -> FailureCode:
        return world.labels.get(referral.id, FailureCode.F00_NO_FAULT)

    def case(case_id: str, referral: Referral, kind: str | None) -> EvalCase:
        code = true_code(referral)
        spec = scenarios[code]
        cs_hears = distractors[kind].cs_hears if kind else spec.cs_hears
        reference = words.reference(referral)
        return EvalCase(
            case_id=case_id,
            question=words.question(referral, reference, spec.leg, cs_hears),
            reference=reference,
            composer_id=referral.id,
            hub_id=referral.hub_referral_id,
            code=code,
            distractor=kind,
            expected_evidence=[
                ExpectedRecord(table=e.table, record_id=e.record_id, source=e.source)
                for e in expected_evidence(world, referral.id, kind)
            ],
            escalate_to_engineering=spec.escalate_to_engineering,
        )

    cases: list[EvalCase] = []
    for code in FailureCode:
        clean = [
            r for r in world.referrals if true_code(r) is code and r.id not in world.distractors
        ]
        for i, referral in enumerate(_spread(clean, per_code, rng, code.value), start=1):
            cases.append(case(f"{code.value[:3]}-{i}", referral, None))
    for kind, n in distractor_plan.items():
        tagged = [r for r in world.referrals if world.distractors.get(r.id) == kind]
        for i, referral in enumerate(_spread(tagged, n, rng, kind), start=1):
            cases.append(case(f"D-{kind}-{i}", referral, kind))
    return Dataset(version=version, world=config_document(config), cases=cases)
