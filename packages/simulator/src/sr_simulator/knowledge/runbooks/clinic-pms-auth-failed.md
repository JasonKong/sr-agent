---
slug: clinic-pms-auth-failed
error_code: F07_CLINIC_PMS_AUTH_FAILED
title: Clinic PMS authorisation failed
---
# Clinic PMS authorisation failed

## Symptoms

- The specialist says the referral never arrived in their PMS, although email and portal notices did.
- Usually several recent referrals to the same clinic are affected, starting at one point in time.

## How to confirm

- Clinic integration: auth status `EXPIRED`. The token expiry time is when the trouble started.
- The `CLINIC_PMS` delivery is `FAILED` with last error code `AUTH_TOKEN_EXPIRED`, and has **no** delivery attempts. Authentication fails before a request is sent.
- `hub-worker` log: ERROR `CLINIC_PMS_AUTH_FAILED` with `AUTH_TOKEN_EXPIRED`.
- Email and portal deliveries succeeded.

Check the time of this referral's push. A delivery that happened **before** the token expired succeeded and is not affected. Likewise, a single 401 followed by `TOKEN_REFRESHED` and a successful retry is not this fault.

## Why it happens

Each clinic integration authenticates with a token. When it expires, and is not refreshed, every push to that clinic fails until the clinic re-authorises.

## What to do

Ask the clinic to re-authorise its PMS connection, then resend every affected referral since the expiry time.

## When to escalate

If re-authorising does not fix it.
