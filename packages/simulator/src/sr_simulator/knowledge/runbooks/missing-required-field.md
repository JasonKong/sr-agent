---
slug: missing-required-field
error_code: F01_MISSING_REQUIRED_FIELD
title: Referral fails validation
---
# Referral fails validation

## Symptoms

- The GP gets an error as soon as they submit; the referral never generates.
- Composer status is `ERROR` with a critical error level, and there is no Hub record.

## How to confirm

- Composer timeline: a `VALIDATION_FAILED` event whose sub type names the field, such as `date_of_birth` or `synthetic_nhi`.
- `composer-web` log: an ERROR line with error code `VALIDATION_FAILED`.
- The patient field shows as empty or not in `DD-MM-YYYY` format. Never read out the value itself.

## Why it happens

Composer requires the patient's NHI and date of birth in `DD-MM-YYYY` format. Some PMS records hold dates in other formats, or have the field blank.

## What to do

Ask the GP to correct the field in their PMS and submit again.

## When to escalate

Only if the field is correct in the PMS but validation still fails.
