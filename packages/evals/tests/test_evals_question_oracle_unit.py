"""The question oracle behind `sr-evals serve-oracle`: answers from the answer key."""

import asyncio
import json
from typing import Any

import pytest

from sr_agent.model import ModelReply, ModelRequest
from sr_db.domain import FailureCode
from sr_evals.oracle import QuestionOracle
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.scenario import load_distractors, load_scenarios


@pytest.fixture(scope="module")
def world() -> World:
    return generate(SimConfig(seed=7, practices=8, clinics=14, referrals=900))


def ask(oracle: QuestionOracle, question: str, *, turns: int = 0) -> ModelReply:
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    for _ in range(turns):
        messages += [{"role": "assistant", "content": []}, {"role": "user", "content": []}]
    return asyncio.run(
        oracle.complete(ModelRequest(system="", tools=[], messages=messages, max_tokens=100))
    )


def test_it_looks_up_the_named_referral_then_answers_with_its_label(world: World) -> None:
    referral = next(r for r in world.referrals if r.id in world.labels and r.hub_referral_id)
    oracle = QuestionOracle(world)
    question = f"Why did {referral.hub_referral_id} fail?"  # a Hub id works too

    first = ask(oracle, question)
    assert first.stop_reason == "tool_use"
    assert {b["input"].get("referral_id") for b in first.content} >= {referral.id}

    final = ask(oracle, question, turns=2)
    assert final.stop_reason == "end_turn"
    assert final.model == "oracle"
    assert json.loads(final.content[0]["text"])["root_cause_code"] == world.labels[referral.id]


def answer_for(world: World, referral_id: str) -> dict[str, Any]:
    reply = ask(QuestionOracle(world), f"What happened to {referral_id}?", turns=2)
    answer: dict[str, Any] = json.loads(reply.content[0]["text"])
    return answer


def test_it_words_the_answer_from_the_scenario_for_a_demo(world: World) -> None:
    # The console demo runs on this oracle, so its answer reads like one: the scenario's
    # root cause and next step, a draft reply, and what each record shows.
    referral = next(
        r for r in world.referrals if r.id in world.labels and r.id not in world.distractors
    )
    scenario = load_scenarios()[world.labels[referral.id]]
    answer = answer_for(world, referral.id)

    assert answer["summary"] == " ".join(scenario.root_cause.split())
    assert answer["recommended_action"] == " ".join(scenario.recommended_action.split())
    assert referral.id in answer["draft_customer_reply"]
    details = [e["detail"] for e in answer["evidence"]]
    assert details
    assert "expected evidence" not in details
    assert all(d.strip() for d in details)


def test_evidence_details_name_the_values_the_scenario_looks_for(world: World) -> None:
    # F07's delivery evidence is a CLINIC_PMS delivery that FAILED with AUTH_TOKEN_EXPIRED.
    referral = next(
        r
        for r in world.referrals
        if world.labels.get(r.id) == FailureCode.F07_CLINIC_PMS_AUTH_FAILED
        and r.id not in world.distractors
    )
    details = " | ".join(e["detail"] for e in answer_for(world, referral.id)["evidence"])
    assert "AUTH_TOKEN_EXPIRED" in details
    assert "status FAILED" in details


def test_a_distractor_case_says_why_it_looked_wrong(world: World) -> None:
    referral_id, kind = next(iter(world.distractors.items()))
    answer = answer_for(world, referral_id)
    assert " ".join(load_distractors()[kind].trap.split()) in answer["summary"]


def test_without_an_id_it_says_there_is_not_enough_evidence(world: World) -> None:
    reply = ask(QuestionOracle(world), "Something is wrong somewhere")
    answer = json.loads(reply.content[0]["text"])
    assert reply.stop_reason == "end_turn"
    assert answer["root_cause_code"] is None
    assert answer["confidence"] == "low"
