---
slug: attachment-fetch-failed
error_code: F05_ATTACHMENT_FETCH_FAILED
title: Hub could not fetch the referral PDF
---
# Hub could not fetch the referral PDF

## Symptoms

- Composer says the referral was sent, but the specialist never got it.
- Composer status is `SENT` and never moves to `RECEIVED`.

## How to confirm

- Hub referral exists but has no accepted time and no routing mode.
- Hub file: fetch status `FAILED`, with an error such as "HTTP 403: signed download URL has expired".
- No deliveries.
- Composer timeline: `CHECK_RECEIVED` (Hub has not accepted it), then `AUTO_RESEND`.
- `hub-worker` log: `FILE_FETCH_FAILED`.

## Why it happens

The Hub downloads the PDF from Composer using a short-lived signed URL. If the download fails, the Hub never accepts the referral. Composer checks after about an hour and resends once.

## What to do

Resend the referral from Composer.

## When to escalate

If the resend fails the same way, escalate to engineering with the correlation id.
