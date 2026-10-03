---
slug: pdf-too-large
error_code: F02_PDF_TOO_LARGE
title: Referral PDF too large
---
# Referral PDF too large

## Symptoms

- The referral fails while generating. The GP attached several documents, often scans or photos.
- Composer status is `ERROR`; nothing reaches the Hub.

## How to confirm

- Composer timeline: `PDF_TOO_LARGE`, then `PDF_COMPRESSION_RETRY`, then `PDF_GENERATION_FAILED`.
- Source documents with large sizes (several MB each).
- `composer-worker` log: error code `PDF_TOO_LARGE`.

## Why it happens

The referral PDF has a 15 MB limit. Composer retries once with compression; if the PDF is still over the limit, generation fails.

## What to do

Ask the GP to remove or reduce the attached documents (for example a summary instead of full scans) and submit again.

## When to escalate

Not usually. Escalate only if a small referral fails with this error.
