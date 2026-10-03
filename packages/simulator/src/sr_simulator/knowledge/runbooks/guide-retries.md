---
slug: guide-retries
error_code: null
title: "Guide: automatic resends and retries"
---
# Guide: automatic resends and retries

## Composer hand-off resend

If the Hub has not confirmed a referral, Composer resends it once, 55 to 95 minutes after the first send. You will see `AUTO_RESEND` in the Composer timeline.

- If the resend succeeds, the referral continues normally. An earlier `HUB_SEND_FAILED` is history, not a fault.
- If it fails too, the referral stays stuck: see the Hub unavailable runbook.

## Clinic PMS retries

- **Timeouts.** The Hub retries a timed-out push after 1 minute and again after 5 minutes, then gives up.
- **Expired token.** On a 401, the Hub tries to refresh the token and retries within seconds. If the refresh works, you will see `TOKEN_REFRESHED` and a successful second attempt, and the delivery is fine even though its last error code says `AUTH_TOKEN_EXPIRED`. If the token cannot be refreshed, the push fails before any attempt is recorded.

## Return callbacks

Callbacks from the Hub to Composer are sent within seconds of a recipient action.
