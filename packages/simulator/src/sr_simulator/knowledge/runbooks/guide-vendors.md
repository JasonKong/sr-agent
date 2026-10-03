---
slug: guide-vendors
error_code: null
title: "Guide: PMS vendors"
---
# Guide: PMS vendors

## GP practice systems

- **Kauri PMS:** used by about half of practices.
- **Harbour PMS:** about a third of practices.
- **Summit PMS:** the rest.

Composer works the same way with all three. It fetches documents with a 30-second timeout, and files responses in the practice's inbox through the practice's PMS connection.

## Clinic systems

- **Kauri PMS**, **Tasman Clinic** and **Ridgeline** all take referrals through a token-based integration. A clinic has at most one.
- On a 401 the Hub tries to refresh the token and retry. If the token cannot be refreshed, the clinic must re-authorise.
- Any of them can reject an import when the patient does not match their records, or time out when their endpoint is slow.

## Tips

- A problem with one clinic's integration affects every referral to that clinic from the moment it starts. Check the clinic's other recent referrals.
- Past tickets for the same clinic can help, but confirm against the current data: the cause may be different this time.
