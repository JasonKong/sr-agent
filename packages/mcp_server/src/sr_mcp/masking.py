"""Patient masking (DESIGN.md section 7.2).

Patient values never reach a tool. The database role cannot read them; the store returns
only a stable reference and whether each field is present. As a last line of defence,
every tool result is scrubbed for anything that looks like an NHI or a date of birth.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from enum import StrEnum

REDACTED = "[REDACTED]"


class FieldStatus(StrEnum):
    PRESENT = "present"
    EMPTY = "empty"
    INVALID = "invalid"


def patient_ref(patient_id: str, salt: str) -> str:
    """A stable, non-reversible reference such as `PT-3f9a1c`."""
    digest = hmac.new(salt.encode(), patient_id.encode(), hashlib.sha256).hexdigest()
    return f"PT-{digest[:6]}"


_PATTERNS = [
    re.compile(r"\bSIM\d{4}\b"),  # synthetic NHI
    re.compile(r"\b[A-HJ-NP-Z]{3}\d{4}\b"),  # NHI, old format (letters never I or O)
    re.compile(r"\b[A-HJ-NP-Z]{3}\d{2}[A-HJ-NP-Z]{2}\b"),  # NHI, new format
    # Dates written day first, as a PMS stores dates of birth. ISO timestamps are untouched.
    re.compile(r"\b(?:0[1-9]|[12]\d|3[01])[-/.](?:0[1-9]|1[0-2])[-/.](?:19|20)\d{2}\b"),
]


def scrub_text(text: str) -> tuple[str, int]:
    hits = 0
    for pattern in _PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        hits += count
    return text, hits


def scrub(value: object) -> tuple[object, int]:
    """Scrub every string inside a JSON-like value; return the copy and the redaction count."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        items = [scrub(item) for item in value]
        return [item for item, _ in items], sum(hits for _, hits in items)
    if isinstance(value, dict):
        pairs = {key: scrub(item) for key, item in value.items()}
        return {key: item for key, (item, _) in pairs.items()}, sum(h for _, h in pairs.values())
    return value, 0
