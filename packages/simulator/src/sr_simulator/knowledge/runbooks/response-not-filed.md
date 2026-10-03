---
slug: response-not-filed
error_code: F11_RESPONSE_NOT_FILED
title: Specialist response not filed for the GP
---
# Specialist response not filed for the GP

## Symptoms

- The GP says the specialist never replied; the clinic says they did.

## How to confirm

- Recipient action `ACKNOWLEDGED` or `DECLINED` exists.
- The return callback to Composer `SUCCEEDED`.
- Composer inbound response: inbox filing status `FAILED`, with a filing error from the GP's PMS.
- Composer timeline: `RESPONSE_FILING_FAILED`; `composer-worker` log error code `INBOX_FILING_FAILED`.

## Why it happens

Composer files specialist responses into the GP's PMS inbox. If the GP's PMS rejects the entry or the session has expired, the response is stored but the GP never sees it.

## What to do

Tell the GP the response exists, ask them to check their PMS connection, and refile the response.

## When to escalate

If refiling fails repeatedly for the same practice.
