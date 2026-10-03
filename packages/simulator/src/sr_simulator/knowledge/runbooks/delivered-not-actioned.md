---
slug: delivered-not-actioned
error_code: F10_DELIVERED_NOT_ACTIONED
title: Delivered but not actioned
---
# Delivered but not actioned

## Symptoms

- The specialist says they never got the referral, and it has been more than a week.
- The GP has had no acknowledgement.

## How to confirm

- Every delivery (`EMAIL`, `PORTAL`, `CLINIC_PMS`) is `SUCCEEDED`.
- No recipient actions, or only `VIEWED`, for more than 7 days.
- Hub referral status `NEW` or `VIEWED`.

Less than a day or two after delivery, an unopened referral is normal, not this fault.

## Why it happens

The referral reached the clinic, but nobody acted on it. It may be in someone else's queue or an unchecked inbox.

## What to do

Send the clinic the proof of delivery (channels and times), and ask them to check their inbox, portal and PMS.

## When to escalate

Not needed.
