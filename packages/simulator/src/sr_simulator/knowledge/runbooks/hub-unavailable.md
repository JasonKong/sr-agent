---
slug: hub-unavailable
error_code: F04_HUB_UNAVAILABLE
title: Hand-off to the Hub failed
---
# Hand-off to the Hub failed

## Symptoms

- The referral is stuck "processing": generated, in the GP's outbox, never sent.
- Composer status stays `GENERATED`; there is no Hub record at all.
- Often several practices report it at the same time.

## How to confirm

- Composer hand-off attempts (`hub_send`): attempt 1 with HTTP 5xx, then attempt 2, 55 to 95 minutes later, also 5xx.
- Composer timeline: `HUB_SEND_FAILED`, `AUTO_RESEND`, `HUB_SEND_FAILED`.
- `composer-worker` log: ERROR `HUB_SEND_FAILED` lines with error code `HUB_UNAVAILABLE` and the HTTP status.
- No `HR-` referral with this referral as its source.

## Why it happens

The Hub returned server errors. Composer resends once automatically about an hour later; if that also fails, it stops.

If the resend succeeded, the referral continues normally and is not a fault.

## What to do

Escalate to engineering with the correlation id. Once the Hub is healthy, resend the referral.

## When to escalate

Always: a Hub that rejects hand-offs affects every practice.
