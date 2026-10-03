"""Unit tests for historical tickets and runbooks (T1.6). No database needed."""

import re
from collections import Counter
from datetime import timedelta

import pytest

from sr_db.domain import FailureCode
from sr_simulator.config import SimConfig
from sr_simulator.distractors import MISLEADING_CODE
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.scenario import load_scenarios
from sr_simulator.tickets import (
    PLACEHOLDERS,
    TICKET_HISTORY_DAYS,
    load_phrase_banks,
    load_runbooks,
    load_styles,
    placeholders,
)

CONFIG = SimConfig(
    seed=7, practices=8, clinics=14, referrals=700, failure_rate=0.2, distractor_rate=0.1
)


@pytest.fixture(scope="module")
def world() -> World:
    return generate(CONFIG)


# --- Sources -----------------------------------------------------------------------------------


def test_one_phrase_bank_per_code_plus_unresolved() -> None:
    assert set(load_phrase_banks()) == {c.value for c in FailureCode} | {"UNRESOLVED"}


def test_phrases_and_styles_use_only_known_placeholders() -> None:
    texts: list[str] = []
    for bank in load_phrase_banks().values():
        texts += bank.titles + bank.reports + bank.short_reports + bank.guesses
        texts += bank.findings + bank.short_findings + bank.resolutions + bank.short_resolutions
        texts += [g.text for g in bank.wrong_guesses] + [g.thing for g in bank.wrong_guesses]
    book = load_styles()
    texts += book.nhi_clauses
    for style in book.styles.values():
        texts += style.description + style.resolution
    for text in texts:
        assert placeholders(text) <= PLACEHOLDERS, text


def test_one_runbook_per_code_and_some_general_guides() -> None:
    runbooks = load_runbooks()
    codes = [r.error_code for r in runbooks if r.error_code is not None]
    assert sorted(codes) == sorted(FailureCode)
    assert sum(r.error_code is None for r in runbooks) >= 5


@pytest.mark.parametrize("code", list(FailureCode), ids=lambda c: c.value[:3])
def test_code_runbooks_have_the_standard_sections(code: FailureCode) -> None:
    (runbook,) = [r for r in load_runbooks() if r.error_code is code]
    headings = set(re.findall(r"^## (.+)$", runbook.body, flags=re.MULTILINE))
    assert {"Symptoms", "How to confirm", "What to do", "When to escalate"} <= headings


@pytest.mark.parametrize("code", list(FailureCode), ids=lambda c: c.value[:3])
def test_runbooks_name_the_evidence_their_scenario_produces(code: FailureCode) -> None:
    # Keeps runbooks in step with the simulator: every event, error code, column value and
    # service named in the scenario's evidence appears in its runbook.
    (runbook,) = [r for r in load_runbooks() if r.error_code is code]
    for evidence in load_scenarios()[code].evidence:
        for value in evidence.where.values():
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and ("_" in item or "-" in item):
                    assert item in runbook.body, f"{code.value} runbook never mentions {item}"


# --- Generated tickets ---------------------------------------------------------------------------


def test_ticket_count_and_every_code_covered(world: World) -> None:
    misleading = sum(kind == "misleading_ticket" for kind in world.distractors.values())
    assert len(world.support_tickets) == CONFIG.tickets + misleading
    codes = {t.root_cause_code for t in world.support_tickets}
    assert codes >= set(FailureCode)
    assert None in codes  # some tickets were never resolved


def test_tickets_are_historical_and_about_archived_referrals(world: World) -> None:
    current = {r.id for r in world.referrals}
    earliest = CONFIG.history_start - timedelta(days=TICKET_HISTORY_DAYS + 3)
    for t in world.support_tickets:
        assert earliest <= t.created_at < CONFIG.history_start, t.id
        assert t.referral_ref is not None
        assert t.referral_ref not in current
        assert int(t.referral_ref.removeprefix("GR-")) < 10001
        if t.resolved_at is not None:
            assert t.created_at <= t.resolved_at <= CONFIG.anchor_time


def test_unresolved_tickets_have_no_cause_or_resolution(world: World) -> None:
    for t in world.support_tickets:
        assert (t.root_cause_code is None) == (t.resolution is None) == (t.resolved_at is None)


def test_ticket_ids_follow_creation_order(world: World) -> None:
    ordered = sorted(world.support_tickets, key=lambda t: int(t.id.removeprefix("tkt_")))
    assert [t.created_at for t in ordered] == sorted(t.created_at for t in ordered)


def test_tickets_fit_the_clinic(world: World) -> None:
    clinics = {c.id: c for c in world.clinics}
    integrated = {i.clinic_id for i in world.clinic_integrations}
    for t in world.support_tickets:
        assert t.clinic_ref in clinics
        if t.root_cause_code is FailureCode.F06_MANUAL_ROUTING:
            assert not clinics[t.clinic_ref].has_active_account
        if t.root_cause_code in MISLEADING_CODE:
            assert t.clinic_ref in integrated


def test_ticket_text_is_fully_rendered_and_names_no_patient(world: World) -> None:
    names = {f"{p.given_name} {p.family_name}" for p in world.patients}
    for t in world.support_tickets:
        text = "\n".join(filter(None, [t.title, t.description, t.resolution]))
        assert "{" not in text, t
        assert "}" not in text, t
        assert not any(name in text for name in names), t


def test_some_tickets_carry_synthetic_nhis_for_masking_tests(world: World) -> None:
    current = {p.synthetic_nhi for p in world.patients}
    found = [m for t in world.support_tickets for m in re.findall(r"SIM\d{4}", t.description)]
    assert len(found) > 20
    assert not set(found) & current


def test_writing_styles_vary(world: World) -> None:
    descriptions = [t.description for t in world.support_tickets]
    resolutions = [t.resolution or "" for t in world.support_tickets]
    assert any(d.startswith("Customer:") for d in descriptions)  # structured
    assert any(d.startswith(("Hi team", "Morning")) for d in descriptions)  # chatty
    assert any(len(d) < 60 for d in descriptions)  # terse
    assert any(r.startswith(("Correction:", "Turned out")) for r in resolutions)  # corrected
    assert any(d.count("GR-0") >= 3 for d in descriptions)  # several referrals
    assert any(r.startswith(("Sorted.", "Had a look")) for r in resolutions)
    assert len(set(descriptions)) > 0.9 * len(descriptions)


# --- Knowledge distractors -----------------------------------------------------------------------


def test_injection_tickets_are_labelled_and_only_they_carry_injections(world: World) -> None:
    injections = load_styles().injections
    labelled = {
        tid for tid, kind in world.knowledge_labels.items() if kind == "injection_in_ticket"
    }
    assert len(labelled) == round(load_styles().injection_share * CONFIG.tickets)
    for t in world.support_tickets:
        carries = any(text in t.description for text in injections)
        assert carries == (t.id in labelled), t.id


def test_misleading_tickets_blame_a_different_cause_at_the_same_clinic(world: World) -> None:
    misleading = {
        t.id: t
        for t in world.support_tickets
        if world.knowledge_labels.get(t.id) == "misleading_ticket"
    }
    cases = [rid for rid, kind in world.distractors.items() if kind == "misleading_ticket"]
    assert cases
    assert len(misleading) == len(cases)
    clinic_of = {r.id: r.recipient_clinic_ref for r in world.referrals}
    for rid in cases:
        code = world.labels[rid]
        tickets = [t for t in misleading.values() if t.clinic_ref == clinic_of[rid]]
        assert any(t.root_cause_code is MISLEADING_CODE[code] for t in tickets), rid
        assert all(t.root_cause_code is not code for t in tickets)


def test_runbook_rows_are_loaded(world: World) -> None:
    assert len(world.runbooks) == len(load_runbooks())
    assert Counter(r.error_code for r in world.runbooks if r.error_code)  # no duplicates below
    codes = [r.error_code for r in world.runbooks if r.error_code is not None]
    assert len(codes) == len(set(codes))
    assert all(r.updated_at <= CONFIG.anchor_time for r in world.runbooks)
