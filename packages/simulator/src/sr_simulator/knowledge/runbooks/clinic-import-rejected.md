---
slug: clinic-import-rejected
error_code: F09_CLINIC_IMPORT_REJECTED
title: Clinic PMS rejected the import
---
# Clinic PMS rejected the import

## Symptoms

- The clinic can see the referral (portal or email) but it is not in the patient's file in their PMS.

## How to confirm

- `CLINIC_PMS` delivery is `FAILED`. Its single attempt returned HTTP 409 with `PATIENT_MISMATCH`, or HTTP 422 with `PATIENT_INCOMPLETE`.
- `hub-worker` log: ERROR `DELIVERY_REJECTED`.
- The specialist may have `VIEWED` it in the portal.

## Why it happens

The clinic's PMS matches incoming referrals to a patient record. A name that does not match the record for that NHI, or missing details, makes it reject the import.

## What to do

Ask the clinic to match the patient manually in their PMS, or to correct the patient record and file the referral.

## When to escalate

Not usually; this is a data issue at the clinic.
