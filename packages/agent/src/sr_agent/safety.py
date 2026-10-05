"""Output safety checks in code (DESIGN.md sections 7.4 and 8).

- **Patient identifiers.** The CS question is masked before the model sees it, and the
  answer is masked before anyone does. Patterns find NHIs and day-first dates of birth;
  names cannot be found by pattern, which is why the MCP server never returns them.
- **Clinical advice** is out of scope: the agent explains what happened to a referral and
  never recommends a specialty, urgency or treatment. A short list of phrases catches the
  obvious cases. It is deliberately simple, so it is a guard rail, not a guarantee; the
  prompt says the same thing, and a human reviews every draft.
"""

from __future__ import annotations

import re
from typing import Any

from sr_agent.diagnosis import Diagnosis
from sr_db.privacy import scrub_text

_ADVICE = [
    # "should see a cardiologist", "must be seen by a surgeon"
    re.compile(
        r"\b(?:should|must|needs? to|ought to)\s+(?:be\s+seen\s+by|see)\s+an?\s+"
        r"\w*(?:ologist|ician|surgeon|specialist|doctor)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:prescrib\w*|antibiotics?|medications?|dosage|doses?)\b", re.IGNORECASE),
    re.compile(
        r"\burgent(?:ly)?\b[^.]*\b(?:clinical|medical)\b|\b(?:clinical|medical)\b[^.]*\burgent",
        re.IGNORECASE,
    ),
    re.compile(r"\btriag(?:e|ed|ing)\b", re.IGNORECASE),
    re.compile(r"\b(?:treatment\s+(?:plan|options?)|surgery)\b", re.IGNORECASE),
]
_CHECKED = ("summary", "recommended_action", "draft_customer_reply")


def scrub_question(question: str) -> tuple[str, int]:
    return scrub_text(question)


def clinical_advice(diagnosis: Diagnosis) -> list[str]:
    """`field: phrase` for each piece of clinical advice found, in field order."""
    flags = []
    for field in _CHECKED:
        text = getattr(diagnosis, field) or ""
        for pattern in _ADVICE:
            match = pattern.search(text)
            if match:
                flags.append(f"{field}: {match.group(0)}")
    return flags


def _scrub(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        pairs = [_scrub(v) for v in value]
        return [v for v, _ in pairs], sum(h for _, h in pairs)
    if isinstance(value, dict):
        items = {k: _scrub(v) for k, v in value.items()}
        return {k: v for k, (v, _) in items.items()}, sum(h for _, h in items.values())
    return value, 0


def mask_identifiers(diagnosis: Diagnosis) -> tuple[Diagnosis, int]:
    data, hits = _scrub(diagnosis.model_dump(mode="json"))
    return Diagnosis.model_validate(data), hits
