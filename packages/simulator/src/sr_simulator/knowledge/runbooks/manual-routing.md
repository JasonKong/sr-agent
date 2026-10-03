---
slug: manual-routing
error_code: F06_MANUAL_ROUTING
title: Referral routed for manual delivery
---
# Referral routed for manual delivery

## Symptoms

- The clinic says it never received the referral, or never receives anything from us.
- Composer shows the referral as received by the Hub, with no errors.

## How to confirm

- Hub referral routing mode is `MANUAL`.
- The clinic has no active account (`has_active_account` is false).
- There are no deliveries and no recipient actions.
- `hub-api` log: `REFERRAL_ROUTED_MANUAL`.

## Why it happens

The Hub only delivers electronically to clinics with an active account. For any other clinic it accepts the referral and routes it as manual: the GP must send it another way.

## What to do

Tell the GP to send the referral by another route. Suggest the clinic activates an account, and pass its details to onboarding.

## When to escalate

Not needed; this is expected behaviour.
