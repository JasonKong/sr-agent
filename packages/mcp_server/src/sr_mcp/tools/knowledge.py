"""Knowledge tools: search past tickets and runbooks, and fetch a runbook (DESIGN.md 7.3).

These answer "how were similar problems solved before?", never "what happened to this
referral?". The facts of a referral come only from the referral and leg tools.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from sr_db.domain import FailureCode
from sr_mcp.adapters.base import KnowledgeHit, RunbookPage
from sr_mcp.deps import Deps
from sr_mcp.guard import NotFound, ToolResult, guarded_tool


class SearchKnowledgeResult(ToolResult):
    hits: list[KnowledgeHit] = Field(
        description="Matching chunks of past tickets and runbooks, best first."
    )


class RunbookResult(ToolResult):
    runbook: RunbookPage


def register(server: MCPServer, deps: Deps) -> None:
    @guarded_tool(server, deps)
    def search_knowledge(
        query: Annotated[
            str,
            Field(
                min_length=3,
                max_length=500,
                description="What to look for, in plain words: the symptom, the error code "
                "or event, the clinic or vendor.",
            ),
        ],
        error_code: Annotated[
            FailureCode | None,
            Field(description="Only knowledge about this root-cause code."),
        ] = None,
        top_k: Annotated[int, Field(ge=1, le=5, description="At most 5.")] = 5,
    ) -> SearchKnowledgeResult:
        """Search past support tickets and runbooks for how similar problems were solved.

        Use this after looking at the referral's own data, to see which root causes match
        the symptoms you found and what CS did about them. Each hit is a chunk of a ticket
        (tkt_...) or a runbook (rb_...) with its root-cause code and a relevance score.

        This is context, never evidence about this referral: never use it to find a
        referral's status or what happened to it. A past ticket about the same clinic may
        describe a different cause; trust the referral's current data over any ticket.
        """
        return SearchKnowledgeResult(
            hits=deps.knowledge.search(
                query, error_code=error_code.value if error_code else None, top_k=top_k
            )
        )

    @guarded_tool(server, deps)
    def get_runbook(
        error_code: Annotated[FailureCode, Field(description="A root-cause code.")],
    ) -> RunbookResult:
        """The runbook for one root-cause code: symptoms, how to confirm, what to do.

        Use it as a checklist when you suspect a code: it says which records confirm the
        diagnosis and which similar-looking cases do not, and what CS should do next.
        """
        page = deps.knowledge.runbook(error_code.value)
        if page is None:
            raise NotFound("runbook", error_code.value)
        return RunbookResult(runbook=page)
