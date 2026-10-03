---
slug: guide-routing
error_code: null
title: "Guide: how the Hub routes referrals"
---
# Guide: how the Hub routes referrals

## Electronic routing

When the receiving clinic has an active account, the Hub routes the referral as `ELECTRONIC` and creates one delivery per channel:

- `EMAIL`: a notice to the clinic.
- `PORTAL`: the referral appears in the specialist portal.
- `CLINIC_PMS`: a push into each PMS the clinic has subscribed through an integration.

A clinic without an integration only gets email and portal.

## Manual routing

When the clinic has no active account, the routing mode is `MANUAL`. The Hub accepts the referral but delivers nothing; the GP must send it another way. See the manual routing runbook.

## Named specialist

A referral can name a specialist or just the clinic. Routing depends only on the clinic.
