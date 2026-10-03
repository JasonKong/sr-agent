# M1 — Simulator: learning note

> Draft by Claude, from the M1 work. Edit freely; it should end up in your own words.

Milestone M1 · Tasks T1.1–T1.6 · PRs #5–#10 · October 2026

## What I built

A reproducible simulation of two cooperating referral systems. It contains labelled failures, traps and a knowledge base, and it is the ground truth everything from M2 onwards is measured against.

| Task | Result |
| --- | --- |
| T1.1 Schemas | 20 tables in `composer`, `hub` and `knowledge`; value lists shared with Python as `StrEnum`s |
| T1.2 Healthy world | Practices, clinics, people and referrals across all four legs, cut at an anchor time |
| T1.3 Failures | F01–F11 injected from YAML scenarios; clinic-level faults (F07, F08) break an integration from a time T |
| T1.4 Logs and cases | 4 JSONL log services as evidence; `sim case` resolves a case's evidence to record and log ids |
| T1.5 Distractors | Recovered failures, misleading current state, recent referrals, prompt injection in a note |
| T1.6 Knowledge | Historical tickets from phrase banks, 17 runbooks, ticket distractors, a public-boundary test |

With the defaults (`make seed`), one world contains:
- 53,900 rows and 37,430 log lines;
- 377 tickets and 17 runbooks;
- 292 labelled failures and 95 distractors.

Generating it takes about 0.5 s.

## The domain in plain words

A GP's referral is composed in **Composer** (the GP side) and handed to the **Hub**. The Hub delivers it to the specialist clinic by email, portal and the clinic's own PMS. The specialist's response comes back the same way and is filed in the GP's inbox.

Each system has its own ids (`GR-` and `HR-`, with unrelated numbering) and its own logs. Only the `correlation_id` ties them together. That is why diagnosis is hard, and why an agent helps.

This is one F07 case, `GR-11854`, across both systems:

```
22:38:04  composer-web    INFO  GR-11854 SUBMITTED              Submitted by the GP
22:38:19  composer-worker INFO  GR-11854 PDF_GENERATED          360553 bytes
22:38:20  composer-worker INFO  GR-11854 SENT_TO_HUB            Hub referral HR-53818
22:38:20  hub-api         INFO  HR-53818 REFERRAL_RECEIVED      Received referral GR-11854 from Composer
22:38:25  hub-api         INFO  HR-53818 REFERRAL_ACCEPTED      Accepted; delivering on 3 channels
22:38:27  composer-web    INFO  GR-11854 HUB_ACCEPTED           Hub accepted the referral
22:38:33  hub-worker      INFO  HR-53818 DELIVERY_SUCCEEDED     EMAIL dl_538181 to clinic-email ...
22:38:48  hub-worker      INFO  HR-53818 DELIVERY_SUCCEEDED     PORTAL dl_538182 to portal ...
22:38:55  hub-worker      ERROR HR-53818 CLINIC_PMS_AUTH_FAILED Clinic PMS ci_17: authentication failed, token expired
```

Composer thinks everything is fine (`RECEIVED`). Email and portal worked. Only the clinic PMS push failed, before any attempt was recorded. The only traces are the delivery row, the integration's `EXPIRED` status and that one log line.

## What I learned

### Designing data for an AI agent to read

- **Prefixed ids make evidence self-describing.** `dl_538183` is obviously a delivery, and `hub-worker:009472` is obviously a log line. Ids like these help a model cite evidence, and they make wrong citations easy to spot.
- **Links between systems are plain ids, not foreign keys.** Real systems lose track of each other (F04: the Hub never got the referral), so the data must be able to show a broken link.
- **Ground truth never goes in the database.** Labels live in `var/sim/labels.json`, because the MCP server must not be able to read the answers.
- **Watch for answer leakage in the knowledge base.** If tickets referred to current referrals, retrieval would hand the agent the answer to the very case being evaluated. Tickets therefore cover an earlier period and archived referrals.

### Reproducible simulation

- **An anchor time instead of the clock.** Everything after "now" has not happened yet, which gives in-progress referrals for free.
- **Plan, then emit.** Every random value is drawn first; rows are produced afterwards. Draws never depend on where the anchor cuts the timeline.
- **One random stream per entity** (`sha256(seed, kind, index)`). Injecting a failure or a distractor never changes any other referral. A test proves that adding distractors leaves every failure label unchanged.

### Shared state is the hard part

Single-referral faults were easy. Clinic-level faults were not: once a clinic's token expires at time T, **every** later push to that clinic must fail. The same invariant test caught two bugs:

1. F08 picked only referrals whose retries had finished, so a referral that had just started timing out looked healthy.
2. A T1.5 distractor (a delayed resend) could push a healthy delivery past T.

Neither bug was visible in example-based tests. **One invariant test that checks the whole world is worth many example tests.**

### Tests that prove something

- **Watch tests fail first, then mutate the code.** Several times I broke the code on purpose (an extra enum value, a missing index, a skipped document) to confirm the right test failed.
- **Passing tests do not mean correct data.** The generator passed every test while event ids were out of time order, and while Hub ids were Composer ids plus 40,000. Both were found only by looking at the data.
- **Content-consistency tests.** Every runbook must name the events and error codes its scenario produces. The test caught a runbook that never mentioned the log where the evidence actually is.
- **Types reveal design problems.** A function returning an untyped dict made every caller cast; it became the typed `CaseRows`.

### Where configuration ends and code begins

YAML says *what* a scenario or distractor is: what CS hears, the root cause, the trap, and the evidence. Code says *how* it happens. Loading validates the YAML against the code (outcome names, legs, parameters, tables and columns), so a typo fails at once instead of producing quietly wrong data.

### Privacy and the public repository, by construction

- Event details and log messages never contain patient values; tests check every line.
- Tickets contain synthetic NHIs and dates of birth, never names, so pattern-based masking is enough and testable.
- A hashed denylist checks every file and all generated text for real organisation names, without the names ever appearing in the repo.

### Process

- **A template database for integration tests.** Migrating once per session and copying a template per test made the tests about twice as fast.
- **Merging stacked PRs.** Deleting a merged base branch *closes* the next PR rather than retargeting it. Retarget first, then merge.

## What I measured

| Measure | Value |
| --- | --- |
| Source / test lines (db + simulator) | about 3,850 / 1,980 |
| Unit / integration tests | 315 in about 5 s / 89 in about 9 s |
| Default world generation | about 0.5 s; `make seed` including the database load about 1.3 s |
| Failures per code (defaults) | about 27 each; F08 is capped at 20 by the number of clinics with integrations |
| Distractors | 95: six kinds, between 12 and 17 each |

## Open questions for M2

- **Tool granularity.** Should `get_referral` return the linked Composer and Hub view in one call, or should the model follow the link itself? Fewer calls mean less to reason about, but less learning about tool design.
- **Log search.** A full scan of 37k JSONL lines per call is probably fine locally. Should `LogSource` index by correlation id at start-up?
- **Where masking lives.** One masking layer for every tool result, or masking per field? And how do we prove that no tool can return a raw patient value?
- **Read-only access.** Add a read-only database role in a migration now (with no access to patient columns), or rely on masking until the AWS milestone?

## Interview talking points

- "I built the evaluation ground truth before the agent: a simulator with labelled failures, distractors and a knowledge base designed not to leak answers. Every later change can be measured."
- "The hardest bugs were in shared state. A clinic-level failure must affect every later referral to that clinic, and a single invariant test over the whole world caught two bugs that example tests missed."
- "The data is designed for an AI reader: self-describing ids, evidence that resolves to real records, and prompt injection planted in free text as part of the test set."
