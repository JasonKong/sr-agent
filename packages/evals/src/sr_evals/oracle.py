"""An oracle "model" for dry runs: it answers from the answer key.

It looks first, with the same tools a careful agent would use (the evidence reachability
test's sequence), so its evidence resolves only if the tools really show it. It then
gives the labelled code and cites the case's expected live records. A dry run
(`sr-evals run --dry-run`) checks the whole pipeline (dataset, MCP server, loop, answer
checks, scorers and report) end to end, at no cost. Anything below 100% is a bug in the
pipeline, not in a model.
"""

from __future__ import annotations

from sr_agent.diagnosis import DiagnosisDraft, EvidenceCitation
from sr_agent.model import Block, ModelReply, ModelRequest, TokenUsage
from sr_evals.dataset import EvalCase


class OracleModel:
    name = "oracle"

    def __init__(self, case: EvalCase, clinic_id: str) -> None:
        self._case = case
        self._clinic_id = clinic_id

    def _tool(self, n: int, name: str, **arguments: object) -> Block:
        return {"type": "tool_use", "id": f"oracle_{n}", "name": name, "input": arguments}

    def _answer(self) -> ModelReply:
        case = self._case
        draft = DiagnosisDraft(
            summary=f"Oracle answer for {case.case_id}.",
            root_cause_code=case.code,
            confidence="high",
            evidence=[
                EvidenceCitation(record_id=e.record_id, detail="expected evidence")
                for e in case.expected_evidence
                if not e.table.startswith("knowledge.")
            ],
            recommended_action="Follow the runbook for this code.",
            draft_customer_reply=None,
            escalate_to_engineering=case.escalate_to_engineering,
        )
        text: Block = {"type": "text", "text": draft.model_dump_json()}
        return ModelReply(
            content=[text], stop_reason="end_turn", usage=TokenUsage(), model=self.name
        )

    async def complete(self, request: ModelRequest) -> ModelReply:
        turn = sum(1 for m in request.messages if m["role"] == "assistant")
        rid = self._case.composer_id
        if request.tool_choice == "none" or turn >= 2:
            return self._answer()
        if turn == 0:
            calls = [
                self._tool(1, "get_referral", referral_id=rid),
                self._tool(2, "get_referral_timeline", referral_id=rid),
                self._tool(3, "get_deliveries", referral_id=rid),
                self._tool(4, "get_return_path", referral_id=rid),
                self._tool(5, "search_logs", referral_id=rid, limit=200),
            ]
        else:
            calls = [self._tool(6, "get_recipient", clinic_id=self._clinic_id)]
        return ModelReply(
            content=calls, stop_reason="tool_use", usage=TokenUsage(), model=self.name
        )
