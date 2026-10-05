"""Patterns for patient identifiers, shared by the MCP server and the agent (DESIGN.md 8).

The last line of defence, after the database role and the adapters: anything shaped like an
NHI (synthetic, or either real format) or a day-first date of birth is replaced. ISO
timestamps and record ids are left alone. Names cannot be found by pattern; they are kept
out by the database role instead.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

PATTERNS = [
    re.compile(r"\bSIM\d{4}\b"),  # synthetic NHI
    re.compile(r"\b[A-HJ-NP-Z]{3}\d{4}\b"),  # NHI, old format (letters never I or O)
    re.compile(r"\b[A-HJ-NP-Z]{3}\d{2}[A-HJ-NP-Z]{2}\b"),  # NHI, new format
    # Dates written day first, as a PMS stores dates of birth. ISO timestamps are untouched.
    re.compile(r"\b(?:0[1-9]|[12]\d|3[01])[-/.](?:0[1-9]|1[0-2])[-/.](?:19|20)\d{2}\b"),
]


def scrub_text(text: str) -> tuple[str, int]:
    """The text with identifiers replaced, and how many were replaced."""
    hits = 0
    for pattern in PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        hits += count
    return text, hits
