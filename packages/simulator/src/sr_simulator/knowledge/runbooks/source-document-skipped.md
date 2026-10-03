---
slug: source-document-skipped
error_code: F03_SOURCE_DOC_SKIPPED
title: Document missing from a delivered referral
---
# Document missing from a delivered referral

## Symptoms

- The specialist received the referral, but says pages or an attachment are missing.
- Everything else looks healthy: delivered, viewed, often acknowledged.

## How to confirm

- Source documents: one has fetch status `FAILED` with an error such as HTTP 406 or a timeout.
- `composer-worker` log: a WARN line `SOURCE_DOC_FETCH_FAILED` naming the document.
- The Composer timeline shows nothing unusual. This failure is silent, so check the documents and the log.

## Why it happens

Composer pulls documents from the GP's PMS while generating the PDF. If one download fails, it carries on without it and only logs a warning.

## What to do

Ask the GP to send the missing document to the clinic directly, or to resend the referral with it attached.

## When to escalate

If the same GP PMS keeps failing to supply documents, raise it with engineering so the vendor can be contacted.
