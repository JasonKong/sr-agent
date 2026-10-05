"""Chunk tickets and runbooks for retrieval (DESIGN.md section 7.3).

- **Tickets** are one chunk each: the description, then the resolution.
- **Runbooks** are split at their `##` sections. Consecutive sections are merged while they
  fit in `max_chars` (2,000 characters is about 500 tokens). A section longer than that is
  split at blank lines. Each chunk keeps the headings it covers.
- **Masking comes first.** Patient identifiers are masked before a chunk exists, so they
  never reach the embedder, the vector store or a search result.

The chunker reads anything shaped like a ticket or runbook row, so it does not depend on the
simulator.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sr_db.domain import KnowledgeSource
from sr_mcp.masking import scrub_text


class TicketLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def referral_ref(self) -> str | None: ...
    @property
    def clinic_ref(self) -> str | None: ...
    @property
    def title(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def resolution(self) -> str | None: ...
    @property
    def root_cause_code(self) -> str | None: ...
    @property
    def created_at(self) -> datetime: ...


class RunbookLike(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def slug(self) -> str: ...
    @property
    def error_code(self) -> str | None: ...
    @property
    def title(self) -> str: ...
    @property
    def body_markdown(self) -> str: ...


@dataclass(frozen=True)
class Chunk:
    id: str
    source_type: KnowledgeSource
    source_id: str
    chunk_no: int
    error_code: str | None
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    redacted: int = 0

    def embedding_text(self) -> str:
        return f"{self.title}\n{self.text}"

    def content_hash(self, embedder: str) -> str:
        payload = json.dumps(
            [embedder, self.title, self.text, self.error_code, self.metadata], sort_keys=True
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _code(value: object) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def ticket_chunk(ticket: TicketLike) -> Chunk:
    title, hits_title = scrub_text(ticket.title)
    body = ticket.description
    if ticket.resolution:
        body += f"\n\nResolution: {ticket.resolution}"
    text, hits_text = scrub_text(body)
    return Chunk(
        id=f"{ticket.id}#0",
        source_type=KnowledgeSource.TICKET,
        source_id=ticket.id,
        chunk_no=0,
        error_code=_code(ticket.root_cause_code),
        title=title,
        text=text,
        metadata={
            "referral_ref": ticket.referral_ref,
            "clinic_ref": ticket.clinic_ref,
            "created_at": ticket.created_at.isoformat(),
        },
        redacted=hits_title + hits_text,
    )


_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _sections(markdown: str) -> list[tuple[list[str], str]]:
    """(headings, body) per `##` section; `###` headings stay inside their section."""
    body = _FRONT_MATTER.sub("", markdown)
    sections: list[tuple[list[str], list[str]]] = []
    for line in body.splitlines():
        if line.startswith("# "):
            continue  # the title, which every chunk carries anyway
        if line.startswith("## "):
            sections.append(([line[3:].strip()], []))
            continue
        if not sections:
            sections.append(([], []))
        if line.startswith("### "):
            sections[-1][0].append(line[4:].strip())
        sections[-1][1].append(line)
    return [(h, "\n".join(lines).strip()) for h, lines in sections if "\n".join(lines).strip()]


def _pieces(headings: list[str], body: str, max_chars: int) -> list[tuple[list[str], str]]:
    """A long section split at blank lines into pieces of at most `max_chars` (a single
    paragraph may be longer). Each piece keeps the `###` heading it sits under."""
    pieces: list[tuple[list[str], str]] = []
    under: list[str] = headings[:1]
    for paragraph in re.split(r"\n\s*\n", body):
        if paragraph.startswith("### "):
            under = [*headings[:1], paragraph[4:].strip()]
        if pieces and len(pieces[-1][1]) + 2 + len(paragraph) <= max_chars:
            current = pieces[-1][0]
            pieces[-1] = (
                current + [h for h in under if h not in current],
                pieces[-1][1] + "\n\n" + paragraph,
            )
        else:
            pieces.append((list(under), paragraph))
    return pieces


def runbook_chunks(runbook: RunbookLike, *, max_chars: int = 2000) -> list[Chunk]:
    # Whole sections that fit merge with their neighbours; pieces of a split section do not.
    groups: list[tuple[list[str], str]] = []
    mergeable: list[bool] = []
    for headings, body in _sections(runbook.body_markdown):
        if len(body) > max_chars:
            for piece in _pieces(headings, body, max_chars):
                groups.append(piece)
                mergeable.append(False)
        elif groups and mergeable[-1] and len(groups[-1][1]) + 2 + len(body) <= max_chars:
            groups[-1] = (groups[-1][0] + headings, groups[-1][1] + "\n\n" + body)
        else:
            groups.append((list(headings), body))
            mergeable.append(True)
    chunks = []
    single = len(groups) == 1
    for n, (headings, body) in enumerate(groups):
        title = runbook.title if single or not headings else f"{runbook.title} > {headings[0]}"
        text, hits = scrub_text(body)
        chunks.append(
            Chunk(
                id=f"{runbook.id}#{n}",
                source_type=KnowledgeSource.RUNBOOK,
                source_id=runbook.id,
                chunk_no=n,
                error_code=_code(runbook.error_code),
                title=title,
                text=text,
                metadata={"slug": runbook.slug, "headings": headings},
                redacted=hits,
            )
        )
    return chunks
