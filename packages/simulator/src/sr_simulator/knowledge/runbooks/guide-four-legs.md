---
slug: guide-four-legs
error_code: null
title: "Guide: the four legs of a referral"
---
# Guide: the four legs of a referral

A referral passes through two systems. Composer is the GP-side app, with ids like `GR-10234`. The Hub is the central platform, with ids like `HR-55210`. The two numbering schemes are independent: always follow the link, never guess it.

## 1. Compose (Composer)

The GP submits; Composer validates the form, fetches documents from the GP's PMS, renders the PDF and files a copy in the GP's outbox. Failures here never reach the Hub.

## 2. Ingest (Composer to Hub)

Composer sends the referral to the Hub. The Hub fetches the PDF, decides the routing mode and accepts it. Composer records `HUB_ACCEPTED` and the status becomes `RECEIVED`.

## 3. Deliver (Hub to clinic)

The Hub delivers on each channel: an email notice, the portal, and a push to each subscribed clinic PMS. Each channel has its own delivery and attempts, so one can fail while others succeed.

## 4. Return (Hub to Composer to GP)

Specialist actions (viewed, acknowledged, declined, messages) are sent back to Composer and filed in the GP's PMS inbox.

## Tips

- Find the failing leg first, then look only at that leg's records.
- The correlation id is shared by both systems and by every log line about the referral.
