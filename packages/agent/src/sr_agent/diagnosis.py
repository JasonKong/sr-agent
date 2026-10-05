"""The diagnosis the agent returns, and the checks its answer must pass (DESIGN.md 7.4).

The model writes a `DiagnosisDraft`: for each piece of evidence, only the record id it saw
and what that record shows. The code derives the record type and the system from the id's
prefix, so the model has less to get wrong and scoring can compare ids exactly.

An evidence id resolves only if it appeared in a tool result during this run. That is
stronger than "the record exists": the model may cite only what it actually looked at.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sr_db.domain import ID_PREFIX, FailureCode, table_for_id

Confidence = Literal["high", "medium", "low"]
System = Literal["composer", "hub", "knowledge"]

# Ids the tools return: referrals (GR-, HR-), table rows (dl_8812) and log lines
# (hub-worker:000123). Patient snapshots never leave the server, so pt_ ids are not evidence.
_ROW_PREFIXES = sorted(
    {p for p in ID_PREFIX.values() if p.endswith("_") and p != "pt_"}, key=len, reverse=True
)
_ID_PATTERN = re.compile(
    r"(?<![\w-])(?:(?:GR|HR)-\d+|(?:"
    + "|".join(re.escape(p) for p in _ROW_PREFIXES)
    + r")\d+|(?:composer|hub)-[a-z]+:\d+)(?![\w-])"
)
_LOG_ID = re.compile(r"(?P<system>composer|hub)-[a-z]+:\d+")


def record_kind(record_id: str) -> tuple[str, System] | None:
    """The record type and system an id names, or None if it cannot be evidence."""
    log = _LOG_ID.fullmatch(record_id)
    if log is not None:
        system: System = "composer" if log.group("system") == "composer" else "hub"
        return "log", system
    table = table_for_id(record_id)
    if table is None or table == "composer.patient_snapshot":
        return None
    schema, name = table.split(".", 1)
    return name, "composer" if schema == "composer" else "hub" if schema == "hub" else "knowledge"


def ids_in(text: str) -> set[str]:
    """Every evidence-shaped id in a piece of tool output."""
    return set(_ID_PATTERN.findall(text))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceCitation(_Strict):
    record_id: str = Field(
        description="The id exactly as it appears in a tool result, such as dl_538183, "
        "GR-10234 or hub-worker:009472."
    )
    detail: str = Field(description="What this record shows, in a few words.")


class DiagnosisDraft(_Strict):
    """What the model writes. Every field is required; optional values are null."""

    summary: str = Field(description="2 to 4 plain-English sentences for a CS team member.")
    root_cause_code: FailureCode | None = Field(
        description="The root-cause code, or null when the evidence does not settle it."
    )
    confidence: Confidence
    evidence: list[EvidenceCitation] = Field(
        description="The records the diagnosis relies on, cited only from tool results."
    )
    recommended_action: str = Field(description="What the CS team member should do next.")
    draft_customer_reply: str | None = Field(
        description="An optional short draft for the CS team member to edit, or null."
    )
    escalate_to_engineering: bool


class Evidence(BaseModel):
    record_type: str
    record_id: str
    system: System
    detail: str


class Diagnosis(BaseModel):
    """The checked diagnosis: every evidence id resolved to a record seen in this run."""

    summary: str
    root_cause_code: FailureCode | None
    confidence: Confidence
    evidence: list[Evidence]
    recommended_action: str
    draft_customer_reply: str | None
    escalate_to_engineering: bool


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop titles, which the API does not need, keeping the rest of the schema as is."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    result: dict[str, Any] = clean(schema)
    return result


def model_output_schema() -> dict[str, Any]:
    """The JSON schema for structured outputs (`output_config.format`)."""
    return _inline(DiagnosisDraft.model_json_schema())


class AnswerCheck(BaseModel):
    diagnosis: Diagnosis | None
    unresolved: list[str]
    problems: list[str]


def check_answer(text: str, *, seen: set[str]) -> AnswerCheck:
    """Validate the model's answer and resolve its evidence against the ids it saw."""
    try:
        draft = DiagnosisDraft.model_validate(json.loads(text))
    except json.JSONDecodeError:
        return AnswerCheck(diagnosis=None, unresolved=[], problems=["the answer is not valid JSON"])
    except ValidationError as error:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "answer" for e in error.errors()})
        return AnswerCheck(
            diagnosis=None,
            unresolved=[],
            problems=[f"the answer does not fit the schema: check {', '.join(fields)}"],
        )

    evidence: list[Evidence] = []
    unresolved: list[str] = []
    for item in draft.evidence:
        kind = record_kind(item.record_id)
        if kind is None or item.record_id not in seen:
            unresolved.append(item.record_id)
            continue
        evidence.append(
            Evidence(
                record_type=kind[0], record_id=item.record_id, system=kind[1], detail=item.detail
            )
        )
    problems = []
    if unresolved:
        problems.append(
            "these evidence ids do not appear in any tool result in this conversation: "
            + ", ".join(unresolved)
        )
    diagnosis = Diagnosis(**(draft.model_dump() | {"evidence": evidence}))
    return AnswerCheck(diagnosis=diagnosis, unresolved=unresolved, problems=problems)
