"""The agent's HTTP API, with a scripted model and the MCP server in-process."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from sr_agent.api import create_app
from sr_agent.audit import MemoryAudit
from sr_agent.model import ModelClient
from sr_agent.testing import FakeModel, answer, reply, tool_use
from sr_agent.toolbox import McpToolbox, ToolsUnavailable, open_toolbox
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps

TOKEN = "api-" + "t" * 40


@asynccontextmanager
async def in_process(url: str, token: str) -> AsyncIterator[McpToolbox]:
    async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
        yield toolbox


def scripted() -> ModelClient:
    return FakeModel(
        reply(tool_use("list_failed_referrals", hours=24)),
        answer(summary="Nothing failed today.", root_cause_code="F00_NO_FAULT"),
    )


def app_with(**overrides: Any) -> tuple[TestClient, MemoryAudit]:
    audit = MemoryAudit()
    options: dict[str, Any] = {
        "model_for": scripted,
        "connect": in_process,
        "audit": audit,
        "mcp_url": "http://mcp",
        "mcp_token": "m" * 40,
        "api_token": None,
    } | overrides
    return TestClient(create_app(**options)), audit


def test_a_question_is_diagnosed_audited_and_returned() -> None:
    client, audit = app_with()
    response = client.post("/diagnoses", json={"question": "Patient SIM0042: anything fail?"})

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("dx_")
    assert body["outcome"] == "answered"
    assert body["incomplete"] is False
    assert body["diagnosis"]["summary"] == "Nothing failed today."
    assert body["question"] == "Patient [REDACTED]: anything fail?"
    assert [c["name"] for c in body["tool_calls"]] == ["list_failed_referrals"]
    assert audit.get(body["id"]) is not None


def test_a_referral_id_is_added_to_the_question() -> None:
    client, _ = app_with()
    body = client.post(
        "/diagnoses", json={"question": "The clinic never got it.", "referral_id": "GR-10234"}
    ).json()
    assert body["question"] == "The clinic never got it. (Referral GR-10234.)"


def test_a_diagnosis_can_be_fetched_and_given_feedback() -> None:
    client, _ = app_with()
    created = client.post("/diagnoses", json={"question": "Anything fail today?"}).json()

    fetched = client.get(f"/diagnoses/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["diagnosis"] == created["diagnosis"]

    feedback = {"rating": "DOWN", "correct_code": "F08_CLINIC_PMS_TIMEOUT", "comment": "timeout"}
    assert client.post(f"/diagnoses/{created['id']}/feedback", json=feedback).status_code == 204
    after = client.get(f"/diagnoses/{created['id']}").json()
    assert [(f["rating"], f["correct_code"]) for f in after["feedback"]] == [
        ("DOWN", "F08_CLINIC_PMS_TIMEOUT")
    ]


def test_history_lists_the_users_diagnoses_newest_first() -> None:
    client, _ = app_with()
    first = client.post("/diagnoses", json={"question": "First question?"}).json()["id"]
    second = client.post("/diagnoses", json={"question": "Second question?"}).json()["id"]

    history = client.get("/diagnoses", params={"limit": 5}).json()
    assert [h["id"] for h in history] == [second, first]
    assert history[0]["root_cause_code"] == "F00_NO_FAULT"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/diagnoses/dx_000000000000", None),
        ("post", "/diagnoses/dx_000000000000/feedback", {"rating": "UP"}),
    ],
)
def test_unknown_diagnoses_are_404(method: str, path: str, body: dict[str, Any] | None) -> None:
    client, _ = app_with()
    response = client.request(method, path, json=body)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"question": "Hi"},
        {"question": "x" * 2001},
        {"question": "Why did it fail?", "referral_id": "10234"},
    ],
)
def test_bad_questions_are_422(body: dict[str, Any]) -> None:
    client, _ = app_with()
    assert client.post("/diagnoses", json=body).status_code == 422


def test_bad_feedback_is_422() -> None:
    client, _ = app_with()
    created = client.post("/diagnoses", json={"question": "Anything fail today?"}).json()
    response = client.post(f"/diagnoses/{created['id']}/feedback", json={"rating": "MEH"})
    assert response.status_code == 422


def test_progress_streams_as_server_sent_events() -> None:
    client, audit = app_with()
    with client.stream(
        "POST",
        "/diagnoses",
        json={"question": "Anything fail today?"},
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            (
                block.split("\n")[0].removeprefix("event: "),
                block.split("\n")[1].removeprefix("data: "),
            )
            for block in response.read().decode().strip().split("\n\n")
        ]

    names = [name for name, _ in events]
    assert names == ["started", "model_call", "tool_call", "model_call", "done"]
    assert json.loads(events[2][1])["name"] == "list_failed_referrals"
    done = json.loads(events[-1][1])
    assert done["outcome"] == "answered"
    assert audit.get(done["id"]) is not None


def test_a_token_is_required_when_configured() -> None:
    client, _ = app_with(api_token=TOKEN)
    question = {"question": "Anything fail today?"}

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/diagnoses", json=question).status_code == 401
    wrong = {"Authorization": "Bearer " + "x" * 44}
    assert client.post("/diagnoses", json=question, headers=wrong).status_code == 401
    right = {"Authorization": f"bearer {TOKEN}"}
    assert client.post("/diagnoses", json=question, headers=right).status_code == 200
    assert client.get("/diagnoses", headers=right).status_code == 200


def test_an_unavailable_service_is_503_with_the_reason() -> None:
    @asynccontextmanager
    async def down(url: str, token: str) -> AsyncIterator[McpToolbox]:
        raise ToolsUnavailable("cannot reach the MCP server at http://mcp")
        yield  # pragma: no cover

    client, _ = app_with(connect=down)
    response = client.post("/diagnoses", json={"question": "Anything fail today?"})
    assert response.status_code == 503
    assert "cannot reach the MCP server" in response.json()["detail"]


def test_requests_over_the_concurrency_limit_are_429() -> None:
    def slow() -> ModelClient:
        return FakeModel(answer(), sleep_s=0.3)

    app = create_app(
        model_for=slow,
        connect=in_process,
        audit=MemoryAudit(),
        mcp_url="http://mcp",
        mcp_token="m" * 40,
        api_token=None,
        max_concurrent=1,
    )

    async def two_at_once() -> list[int]:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://api") as client:
            responses = await asyncio.gather(
                client.post("/diagnoses", json={"question": "First question?"}),
                client.post("/diagnoses", json={"question": "Second question?"}),
            )
        return sorted(r.status_code for r in responses)

    assert asyncio.run(two_at_once()) == [200, 429]


def test_the_openapi_schema_describes_the_console_contract() -> None:
    client, _ = app_with()
    schema = client.get("/openapi.json").json()

    assert {"/diagnoses", "/diagnoses/{diagnosis_id}", "/diagnoses/{diagnosis_id}/feedback"} <= set(
        schema["paths"]
    )
    assert {"DiagnosisRequest", "DiagnosisResponse", "Feedback", "HistoryItem"} <= set(
        schema["components"]["schemas"]
    )


def test_history_limits_are_validated() -> None:
    client, _ = app_with()
    assert client.get("/diagnoses", params={"limit": 0}).status_code == 422
    assert client.get("/diagnoses", params={"limit": 101}).status_code == 422
