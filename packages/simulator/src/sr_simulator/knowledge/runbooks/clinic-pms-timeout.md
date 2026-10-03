---
slug: clinic-pms-timeout
error_code: F08_CLINIC_PMS_TIMEOUT
title: Clinic PMS timing out
---
# Clinic PMS timing out

## Symptoms

- The specialist has not received the referral in their PMS yet.
- Several recent referrals to the same clinic are affected, starting at one point in time.

## How to confirm

- Clinic integration: endpoint status `DEGRADED`. Its updated time is when the trouble started.
- `CLINIC_PMS` delivery with last error code `TIMEOUT`. It is `FAILED` once retries run out, or `PENDING` while they continue.
- Up to three delivery attempts, each with error code `TIMEOUT` and about 30 s latency.
- `hub-worker` log: WARN `DELIVERY_TIMEOUT` for each attempt, then ERROR `DELIVERY_FAILED` ("retries exhausted").

## Why it happens

The clinic's PMS endpoint stopped responding within 30 seconds. The Hub retries after 1 minute and again after 5 minutes, then gives up.

## What to do

Check the PMS vendor's status. Resend once the endpoint is healthy.

## When to escalate

If the vendor reports no incident and timeouts continue.
