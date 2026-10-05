"""The agent's HTTP API, for the CS console (DESIGN.md section 7.4, T5.4).

- `POST /diagnoses` runs one diagnosis and returns it. With `Accept: text/event-stream` it
  streams progress as server-sent events (`started`, `model_call`, `tool_call`, then `done`
  with the result, or `error`).
- `GET /diagnoses/{id}`, `GET /diagnoses` (the user's history) and
  `POST /diagnoses/{id}/feedback` read and annotate the audit.

One demo user for now (sign-in arrives with Cognito, section 9). An optional bearer token
(`SRA_API_TOKEN`) protects every route but `/healthz`; the console's dev server adds it, so
the browser never holds it. Against cost abuse, the number of diagnoses running at once is
capped (429 when busy), and questions are at most 2,000 characters. The MCP server is
connected per request, so the API starts, and reports clearly, even when it is down.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sr_agent.audit import (
    AuditRecord,
    AuditStore,
    Feedback,
    FeedbackRecord,
    HistoryItem,
    UnknownDiagnosis,
)
from sr_agent.diagnosis import Diagnosis
from sr_agent.loop import (
    DEFAULT_LIMITS,
    AgentEvent,
    AgentRun,
    Limits,
    ModelCalled,
    Outcome,
    ToolCalled,
    ToolCallRecord,
    json_log,
    run_agent,
)
from sr_agent.model import ModelClient, ModelUnavailable
from sr_agent.prompts import PROMPT_VERSION
from sr_agent.toolbox import Toolbox, ToolsUnavailable

Connect = Callable[[str, str], AbstractAsyncContextManager[Toolbox]]
USER = "demo"


class DiagnosisRequest(BaseModel):
    question: str = Field(min_length=5, max_length=2000, description="What CS was told.")
    referral_id: str | None = Field(
        default=None, pattern=r"^(GR|HR)-\d+$", description="The referral, if CS has its id."
    )


class DiagnosisResponse(BaseModel):
    id: str
    created_at: datetime
    question: str  # as the agent saw it, with patient identifiers masked
    outcome: Outcome
    incomplete: bool
    diagnosis: Diagnosis | None
    model: str
    served_by: list[str]
    prompt_version: str
    turns: int
    tool_calls: list[ToolCallRecord]
    duration_ms: float
    cost_usd: float | None
    safety_flags: list[str]
    dropped_evidence: list[str]
    feedback: list[FeedbackRecord]


def _response(record: AuditRecord) -> DiagnosisResponse:
    run = record.run
    return DiagnosisResponse(
        id=record.id,
        created_at=record.created_at,
        question=run.question,
        outcome=run.outcome,
        incomplete=run.incomplete,
        diagnosis=run.diagnosis,
        model=run.model,
        served_by=run.served_by,
        prompt_version=run.prompt_version,
        turns=run.turns,
        tool_calls=run.tool_calls,
        duration_ms=run.duration_ms,
        cost_usd=run.cost_usd,
        safety_flags=run.safety_flags,
        dropped_evidence=run.dropped_evidence,
        feedback=record.feedback,
    )


def _event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(
    *,
    model_for: Callable[[], ModelClient],
    connect: Connect,
    audit: AuditStore,
    mcp_url: str,
    mcp_token: str,
    api_token: str | None,
    limits: Limits = DEFAULT_LIMITS,
    prompt_version: str = PROMPT_VERSION,
    max_concurrent: int = 2,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="SR-Agent API",
        version="0.1.0",
        description="Explains why a simulated GP-to-specialist referral failed, with evidence.",
    )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    running = asyncio.Semaphore(max_concurrent)

    def authorised(authorization: Annotated[str | None, Header()] = None) -> None:
        if api_token is None:
            return
        scheme, _, presented = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented, api_token):
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})

    guarded = [Depends(authorised)]

    async def diagnose(question: str, observe: Callable[[AgentEvent], None]) -> AgentRun:
        async with connect(mcp_url, mcp_token) as toolbox:
            return await run_agent(
                question,
                model=model_for(),
                toolbox=toolbox,
                limits=limits,
                observe=observe,
                prompt_version=prompt_version,
            )

    def record(run: AgentRun) -> DiagnosisResponse:
        found = audit.get(audit.record(run, user=USER))
        assert found is not None
        return _response(found)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/diagnoses", response_model=DiagnosisResponse, dependencies=guarded)
    async def create_diagnosis(body: DiagnosisRequest, request: Request) -> Any:
        if running.locked():
            raise HTTPException(429, "busy: too many diagnoses are running; try again shortly")
        question = body.question
        if body.referral_id:
            question = f"{question} (Referral {body.referral_id}.)"

        if "text/event-stream" not in request.headers.get("accept", ""):
            async with running:
                try:
                    run = await diagnose(question, json_log)
                except (ToolsUnavailable, ModelUnavailable) as error:
                    raise HTTPException(503, str(error)) from error
            return record(run)

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def observe(event: AgentEvent) -> None:
            json_log(event)
            match event:
                case ModelCalled():
                    data = {
                        "turn": event.turn,
                        "stop_reason": event.stop_reason,
                        "duration_ms": event.duration_ms,
                    }
                    queue.put_nowait(_event("model_call", data))
                case ToolCalled():
                    queue.put_nowait(_event("tool_call", event.record.model_dump(mode="json")))
                case _:
                    pass

        async def produce() -> None:
            async with running:
                queue.put_nowait(_event("started", {"question": question}))
                try:
                    run = await diagnose(question, observe)
                    queue.put_nowait(_event("done", record(run).model_dump(mode="json")))
                except (ToolsUnavailable, ModelUnavailable) as error:
                    queue.put_nowait(_event("error", {"detail": str(error)}))
                finally:
                    queue.put_nowait(None)

        task = asyncio.create_task(produce())

        async def stream() -> AsyncIterator[str]:
            while (item := await queue.get()) is not None:
                yield item
            await task

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/diagnoses", response_model=list[HistoryItem], dependencies=guarded)
    def history(limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[HistoryItem]:
        return audit.history(USER, limit=limit)

    @app.get("/diagnoses/{diagnosis_id}", response_model=DiagnosisResponse, dependencies=guarded)
    def get_diagnosis(diagnosis_id: str) -> DiagnosisResponse:
        found = audit.get(diagnosis_id)
        if found is None:
            raise HTTPException(404, f"no diagnosis {diagnosis_id}")
        return _response(found)

    @app.post("/diagnoses/{diagnosis_id}/feedback", status_code=204, dependencies=guarded)
    def add_feedback(diagnosis_id: str, feedback: Feedback) -> Response:
        try:
            audit.add_feedback(diagnosis_id, feedback, user=USER)
        except UnknownDiagnosis as error:
            raise HTTPException(404, f"no diagnosis {diagnosis_id}") from error
        return Response(status_code=204)

    return app
