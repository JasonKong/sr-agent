"""The question oracle behind `sr-evals serve-oracle`: answers from the answer key."""

import asyncio
import json
from typing import Any

import pytest

from sr_agent.model import ModelReply, ModelRequest
from sr_evals.oracle import QuestionOracle
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World


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


def test_without_an_id_it_says_there_is_not_enough_evidence(world: World) -> None:
    reply = ask(QuestionOracle(world), "Something is wrong somewhere")
    answer = json.loads(reply.content[0]["text"])
    assert reply.stop_reason == "end_turn"
    assert answer["root_cause_code"] is None
    assert answer["confidence"] == "low"
