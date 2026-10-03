"""Historical support tickets and runbooks: the knowledge sources for RAG (DESIGN.md 7.3).

Tickets are assembled deterministically from hand-written phrase banks
(`knowledge/ticket_phrases/*.yaml`) and writing styles (`knowledge/ticket_styles.yaml`), so
their wording varies like real CS notes without calling a model at seed time.

Tickets are *historical*: created before the database's history window, about archived
referrals (`GR-0nnnn`) that are no longer in the database, and about clinics that are. That
keeps evaluation cases from having their own answer sitting in the knowledge base.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from random import Random
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sr_db.domain import ClinicPmsVendor, FailureCode, GpPmsVendor
from sr_simulator.config import NZ, SimConfig
from sr_simulator.distractors import MISLEADING_CODE
from sr_simulator.plan import ReferralPlan
from sr_simulator.rng import rng_for
from sr_simulator.rows import Clinic, GpPractice, Runbook, SupportTicket, World
from sr_simulator.timeline import after_business_days, business_moment

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
PHRASE_DIR = KNOWLEDGE_DIR / "ticket_phrases"
RUNBOOK_DIR = KNOWLEDGE_DIR / "runbooks"
STYLE_FILE = KNOWLEDGE_DIR / "ticket_styles.yaml"

TICKET_HISTORY_DAYS = 730  # tickets span two years before the database's history window

VENDOR_NAMES: dict[str, str] = {
    GpPmsVendor.KAURI_PMS: "Kauri PMS",
    GpPmsVendor.HARBOUR_PMS: "Harbour PMS",
    GpPmsVendor.SUMMIT_PMS: "Summit PMS",
    ClinicPmsVendor.TASMAN_CLINIC: "Tasman Clinic",
    ClinicPmsVendor.RIDGELINE: "Ridgeline",
}

# Every placeholder a phrase or style template may use.
PLACEHOLDERS = {
    "practice", "clinic", "vendor", "gr", "gr2", "gr3", "days", "nhi", "dob", "nhi_clause",
    "report", "short_report", "guess", "wrong_guess", "wrong_thing",
    "finding", "short_finding", "resolution", "short_resolution",
}  # fmt: skip


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WrongGuess(_Strict):
    text: str
    thing: str


class PhraseBank(_Strict):
    code: FailureCode | Literal["UNRESOLVED"]
    weight: int = Field(gt=0)
    clinic: Literal["account", "no_account", "integration"]
    vendor_side: Literal["gp", "clinic", "none"]
    titles: list[str] = Field(min_length=1)
    reports: list[str] = Field(min_length=1)
    short_reports: list[str] = Field(min_length=1)
    guesses: list[str] = Field(min_length=1)
    wrong_guesses: list[WrongGuess] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    short_findings: list[str] = Field(default_factory=list)
    resolutions: list[str] = Field(default_factory=list)
    short_resolutions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _resolved_banks_have_outcomes(self) -> PhraseBank:
        if self.code != "UNRESOLVED":
            for name in ("wrong_guesses", "findings", "short_findings", "resolutions"):
                if not getattr(self, name):
                    raise ValueError(f"{self.code}: {name} must not be empty")
            if not self.short_resolutions:
                raise ValueError(f"{self.code}: short_resolutions must not be empty")
        return self


class Style(_Strict):
    weight: int = Field(gt=0)
    lowercase_title: bool = False
    description: list[str] = Field(min_length=1)
    resolution: list[str] = Field(min_length=1)


class StyleBook(_Strict):
    nhi_share: float = Field(ge=0, le=1)
    nhi_clauses: list[str] = Field(min_length=1)
    injection_share: float = Field(ge=0, le=0.2)
    injections: list[str] = Field(min_length=1)
    styles: dict[str, Style]


class RunbookDoc(_Strict):
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    error_code: FailureCode | None
    title: str
    body: str


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@cache
def load_phrase_banks(directory: Path = PHRASE_DIR) -> dict[str, PhraseBank]:
    banks: dict[str, PhraseBank] = {}
    for path in sorted(directory.glob("*.yaml")):
        bank = PhraseBank.model_validate(yaml.safe_load(path.read_text()))
        key = bank.code.value if isinstance(bank.code, FailureCode) else bank.code
        if path.stem != key:
            raise ValueError(f"{path.name} holds {key}; name the file {key}.yaml")
        banks[key] = bank
    return banks


@cache
def load_styles(path: Path = STYLE_FILE) -> StyleBook:
    return StyleBook.model_validate(yaml.safe_load(path.read_text()))


def parse_runbook(text: str) -> RunbookDoc:
    if not text.startswith("---\n"):
        raise ValueError("a runbook must start with YAML front matter between --- lines")
    front, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(front)
    return RunbookDoc.model_validate({**meta, "body": body.lstrip("\n")})


@cache
def load_runbooks(directory: Path = RUNBOOK_DIR) -> list[RunbookDoc]:
    docs = []
    for path in sorted(directory.glob("*.md")):
        doc = parse_runbook(path.read_text())
        if path.stem != doc.slug:
            raise ValueError(f"{path.name} holds {doc.slug}; name the file {doc.slug}.md")
        docs.append(doc)
    return docs


# --- Assembling tickets -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Draft:
    created_at: datetime
    order: int
    clinic_ref: str
    referral_ref: str
    title: str
    description: str
    resolution: str | None
    root_cause: FailureCode | None
    resolved_at: datetime | None
    label: str | None  # knowledge distractor kind, if any


def _archived_id(rng: Random) -> str:
    return f"GR-0{rng.randint(1000, 9999)}"  # below GR-10001: no longer in the database


def _day_between(rng: Random, start: date, end: date) -> date:
    day = start + timedelta(days=rng.randrange(max((end - start).days, 1)))
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _draft(
    bank: PhraseBank,
    rng: Random,
    config: SimConfig,
    world: World,
    clinic: Clinic,
    created_at: datetime,
    order: int,
    injection: str | None,
    label: str | None,
) -> _Draft:
    book = load_styles()
    practice: GpPractice = rng.choice(world.gp_practices)
    integration = next((i for i in world.clinic_integrations if i.clinic_id == clinic.id), None)
    if bank.vendor_side == "clinic" and integration is not None:
        vendor = VENDOR_NAMES[integration.vendor]
    else:
        vendor = VENDOR_NAMES[practice.pms_vendor]
    first_archived_nhi = min(config.referrals + 1, 9999)
    born = config.anchor_time.date() - timedelta(days=rng.randint(365, 90 * 365))
    base = {
        "practice": practice.name,
        "clinic": clinic.name,
        "vendor": vendor,
        "gr": _archived_id(rng),
        "gr2": _archived_id(rng),
        "gr3": _archived_id(rng),
        "days": str(rng.randint(7, 21)),
        "nhi": f"SIM{rng.randint(first_archived_nhi, 9999):04d}",
        "dob": born.strftime("%d-%m-%Y"),
    }
    base["nhi_clause"] = (
        rng.choice(book.nhi_clauses).format(**base) if rng.random() < book.nhi_share else ""
    )
    allowed = book.styles if bank.code != "UNRESOLVED" else {"terse", "chatty", "structured"}
    names = [n for n in book.styles if n in allowed]
    style_name = rng.choices(names, weights=[book.styles[n].weight for n in names])[0]
    style = book.styles[style_name]

    def pick(options: list[str]) -> str:
        return rng.choice(options).format(**base) if options else ""

    wrong = rng.choice(bank.wrong_guesses) if bank.wrong_guesses else None
    phrases = {
        "report": pick(bank.reports),
        "short_report": pick(bank.short_reports),
        "guess": pick(bank.guesses),
        "wrong_guess": wrong.text.format(**base) if wrong else "",
        "wrong_thing": wrong.thing.format(**base) if wrong else "",
        "finding": pick(bank.findings),
        "short_finding": pick(bank.short_findings),
        "resolution": pick(bank.resolutions),
        "short_resolution": pick(bank.short_resolutions),
    }
    title = pick(bank.titles)
    if style.lowercase_title:
        title = title.lower()
    description = rng.choice(style.description).format(**base, **phrases)
    if injection is not None:
        description = f"{description}\n\n{injection}"
    if bank.code == "UNRESOLVED":
        return _Draft(
            created_at, order, clinic.id, base["gr"], title, description, None, None, None, label
        )
    assert isinstance(bank.code, FailureCode)
    resolution = rng.choice(style.resolution).format(**base, **phrases)
    resolved_at = after_business_days(rng, created_at, 5)
    return _Draft(
        created_at,
        order,
        clinic.id,
        base["gr"],
        title,
        description,
        resolution,
        bank.code,
        resolved_at,
        label,
    )


def _clinic_for(bank: PhraseBank, rng: Random, world: World) -> Clinic:
    with_account = [c for c in world.clinics if c.has_active_account]
    integrated = {i.clinic_id for i in world.clinic_integrations}
    pools = {
        "account": with_account,
        "no_account": [c for c in world.clinics if not c.has_active_account] or with_account,
        "integration": [c for c in with_account if c.id in integrated] or with_account,
    }
    return rng.choice(pools[bank.clinic])


def build_knowledge(config: SimConfig, world: World, plans: list[ReferralPlan]) -> None:
    """Add historical tickets (and knowledge distractors) and runbooks to `world`."""
    banks = load_phrase_banks()
    book = load_styles()
    keys = sorted(banks)
    weights = [banks[k].weight for k in keys]
    window_end = config.history_start.astimezone(NZ).date() - timedelta(days=1)
    window_start = window_end - timedelta(days=TICKET_HISTORY_DAYS)

    injected = set(
        rng_for(config.seed, "ticket-injections").sample(
            range(1, config.tickets + 1), round(book.injection_share * config.tickets)
        )
    )
    drafts: list[_Draft] = []
    for n in range(1, config.tickets + 1):
        rng = rng_for(config.seed, "ticket", n)
        bank = banks[rng.choices(keys, weights=weights)[0]]
        created = business_moment(rng, _day_between(rng, window_start, window_end))
        clinic = _clinic_for(bank, rng, world)
        injection = rng.choice(book.injections) if n in injected else None
        label = "injection_in_ticket" if injection else None
        drafts.append(_draft(bank, rng, config, world, clinic, created, n, injection, label))

    # A recent-looking past ticket for the same clinic, blaming a different cause.
    for plan in plans:
        if plan.distractor != "misleading_ticket":
            continue
        rng = rng_for(config.seed, "misleading-ticket", plan.index)
        bank = banks[MISLEADING_CODE[plan.label].value]
        day = _day_between(rng, window_end - timedelta(days=120), window_end)
        created = business_moment(rng, day)
        order = config.tickets + plan.index
        drafts.append(
            _draft(
                bank, rng, config, world, plan.clinic.row, created, order, None, "misleading_ticket"
            )
        )

    drafts.sort(key=lambda d: (d.created_at, d.order))
    for number, d in enumerate(drafts, start=1):
        ticket_id = f"tkt_{number}"
        world.support_tickets.append(
            SupportTicket(
                id=ticket_id,
                referral_ref=d.referral_ref,
                clinic_ref=d.clinic_ref,
                title=d.title,
                description=d.description,
                resolution=d.resolution,
                root_cause_code=d.root_cause,
                created_at=d.created_at,
                resolved_at=d.resolved_at,
            )
        )
        if d.label is not None:
            world.knowledge_labels[ticket_id] = d.label

    for number, doc in enumerate(load_runbooks(), start=1):
        world.runbooks.append(
            Runbook(
                id=f"rb_{number}",
                slug=doc.slug,
                error_code=doc.error_code,
                title=doc.title,
                body_markdown=doc.body,
                updated_at=config.anchor_time - timedelta(days=14 + number),
            )
        )
