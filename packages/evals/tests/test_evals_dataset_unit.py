"""The eval dataset: built from the simulator's ground truth, deterministic, and frozen."""

from collections import Counter

import pytest

from sr_db.domain import FailureCode
from sr_evals.dataset import (
    DATASET_V1,
    DATASETS,
    DISTRACTOR_PLAN,
    HOLDOUT_SEED,
    build_dataset,
    config_from,
    load_dataset,
    world_for,
)
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World

SMALL = SimConfig(seed=7, practices=8, clinics=14, referrals=900)
SMALL_PLAN = {"recovered_pms_auth": 1, "in_progress": 1, "injection_in_note": 1}


@pytest.fixture(scope="module")
def world() -> World:
    return generate(SMALL)


def test_each_code_gets_its_cases_and_each_distractor_its_share(world: World) -> None:
    dataset = build_dataset(world, SMALL, per_code=2, distractor_plan=SMALL_PLAN)

    clean = [c for c in dataset.cases if c.distractor is None]
    assert Counter(c.code for c in clean) == dict.fromkeys(FailureCode, 2)
    assert Counter(c.distractor for c in dataset.cases if c.distractor) == SMALL_PLAN
    assert len({c.composer_id for c in dataset.cases}) == len(dataset.cases)
    assert len({c.case_id for c in dataset.cases}) == len(dataset.cases)


def test_labels_and_evidence_come_from_the_ground_truth(world: World) -> None:
    dataset = build_dataset(world, SMALL, per_code=2, distractor_plan=SMALL_PLAN)

    for case in dataset.cases:
        assert case.code == world.labels.get(case.composer_id, FailureCode.F00_NO_FAULT)
        assert case.distractor == world.distractors.get(case.composer_id)
        referral = next(r for r in world.referrals if r.id == case.composer_id)
        assert case.hub_id == referral.hub_referral_id
        if case.code is not FailureCode.F00_NO_FAULT:
            assert case.expected_evidence, case.case_id
    assert any(e.source == "distractor" for c in dataset.cases for e in c.expected_evidence)


def test_questions_name_the_referral_one_of_three_ways(world: World) -> None:
    dataset = build_dataset(world, SMALL, per_code=4, distractor_plan=SMALL_PLAN)

    kinds = Counter(c.reference for c in dataset.cases)
    assert set(kinds) == {"composer_id", "hub_id", "clinic_and_date"}
    for case in dataset.cases:
        if case.reference == "composer_id":
            assert case.composer_id in case.question
        elif case.reference == "hub_id":
            assert case.hub_id is not None
            assert case.hub_id in case.question
            assert case.composer_id not in case.question
        else:
            assert case.composer_id not in case.question
            assert case.hub_id is None or case.hub_id not in case.question
    assert len({c.question for c in dataset.cases}) == len(dataset.cases)


def test_questions_never_contain_patient_details(world: World) -> None:
    dataset = build_dataset(world, SMALL, per_code=4, distractor_plan=SMALL_PLAN)
    text = " ".join(c.question for c in dataset.cases)

    for patient in world.patients:
        assert f"{patient.given_name} {patient.family_name}" not in text
        if patient.synthetic_nhi:
            assert patient.synthetic_nhi not in text


def test_building_twice_gives_the_same_dataset(world: World) -> None:
    first = build_dataset(world, SMALL, per_code=2, distractor_plan=SMALL_PLAN)
    second = build_dataset(generate(SMALL), SMALL, per_code=2, distractor_plan=SMALL_PLAN)
    assert first == second


def test_the_frozen_dataset_matches_the_default_world() -> None:
    """If the simulator changes, this fails: rebuild v1 on purpose (`sr-evals build`)."""
    frozen = load_dataset(DATASET_V1)
    config = SimConfig()
    rebuilt = build_dataset(world_for(frozen), config)

    assert frozen == rebuilt
    assert len(frozen.cases) == 75
    assert sum(DISTRACTOR_PLAN.values()) == 15
    assert frozen.world["seed"] == config.seed


def test_the_frozen_holdout_set_matches_its_own_world() -> None:
    """The test set: another seed, built the same way, its own wording and selection."""
    frozen = load_dataset(DATASETS["holdout_v1"])

    assert frozen.version == "holdout_v1"
    assert frozen.world["seed"] == HOLDOUT_SEED != SimConfig().seed
    assert frozen == build_dataset(
        world_for(frozen), config_from(frozen.world), version="holdout_v1"
    )
    assert len(frozen.cases) == 75
    # Another world: ids can repeat across the two, but no question does.
    v1 = load_dataset(DATASET_V1)
    assert not {c.question for c in v1.cases} & {c.question for c in frozen.cases}


def test_neutral_questions_do_not_presuppose_a_fault(world: World) -> None:
    dataset = build_dataset(world, SMALL, per_code=4, distractor_plan=SMALL_PLAN)
    no_fault = [c for c in dataset.cases if c.code is FailureCode.F00_NO_FAULT and not c.distractor]

    assert no_fault
    for case in no_fault:
        assert "why?" not in case.question
        assert "went wrong" not in case.question


def test_cases_of_one_code_come_from_different_clinics_when_they_can(world: World) -> None:
    frozen = build_dataset(world, SMALL, per_code=4, distractor_plan=SMALL_PLAN)
    clinic_of = {r.id: r.recipient_clinic_ref for r in world.referrals}
    for code in FailureCode:
        cases = [c for c in frozen.cases if c.code is code and c.distractor is None]
        clinics = {clinic_of[c.composer_id] for c in cases}
        available = {
            clinic_of[r.id]
            for r in world.referrals
            if world.labels.get(r.id, FailureCode.F00_NO_FAULT) is code
            and r.id not in world.distractors
        }
        assert len(clinics) == min(len(cases), len(available)), code


def test_a_question_by_clinic_and_day_points_to_one_referral_only(world: World) -> None:
    dataset = build_dataset(world, SMALL, per_code=4, distractor_plan=SMALL_PLAN)
    by_id = {r.id: r for r in world.referrals}
    named = [c for c in dataset.cases if c.reference == "clinic_and_date"]

    assert named
    for case in named:
        referral = by_id[case.composer_id]
        day = referral.created_at.astimezone(SMALL.anchor_time.tzinfo).date()
        neighbours = [
            r
            for r in world.referrals
            if r.practice_id == referral.practice_id
            and r.recipient_clinic_ref == referral.recipient_clinic_ref
            and abs(r.created_at.astimezone(SMALL.anchor_time.tzinfo).date() - day).days <= 1
        ]
        assert neighbours == [referral], case.case_id
