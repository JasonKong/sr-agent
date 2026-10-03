---
slug: no-fault
error_code: F00_NO_FAULT
title: Confirming a referral is healthy
---
# Confirming a referral is healthy

Many "did they get it?" questions are about referrals that are fine. Check before assuming a fault.

## Symptoms

- The GP has not heard back and wants to know whether the specialist received the referral.
- The patient was told to expect contact and has not had any yet.

## How to confirm

1. Look up the referral in Composer (`GR-`) and follow `hub_referral_id` to the Hub record (`HR-`).
2. Check the Hub referral is accepted and its routing mode is `ELECTRONIC`.
3. Check every delivery (`EMAIL`, `PORTAL` and each `CLINIC_PMS`) is `SUCCEEDED`.
4. Check recipient actions: `VIEWED`, then `ACKNOWLEDGED` or `DECLINED`, and that the response was filed in the GP inbox.

A referral delivered in the last day or two that has not been viewed yet is normal. Clinics usually act within a few business days.

## What to do

Confirm the status to the customer, with delivery times if useful. No action is needed.

## When to escalate

Never, if the checks above pass. Earlier errors that later recovered (for example a token that was refreshed, or a resend that succeeded) are not faults.
