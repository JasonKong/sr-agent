import type { FailureCode } from "./api/client";

/** Plain-English titles for the root-cause codes, as the runbooks name them. */
export const CODE_TITLES: Record<FailureCode, string> = {
  F00_NO_FAULT: "No fault found",
  F01_MISSING_REQUIRED_FIELD: "A required field is missing",
  F02_PDF_TOO_LARGE: "The referral PDF is too large",
  F03_SOURCE_DOC_SKIPPED: "A source document was left out",
  F04_HUB_UNAVAILABLE: "The Hub was unavailable",
  F05_ATTACHMENT_FETCH_FAILED: "The Hub could not fetch the PDF",
  F06_MANUAL_ROUTING: "Routed for manual delivery",
  F07_CLINIC_PMS_AUTH_FAILED: "Clinic PMS authorisation failed",
  F08_CLINIC_PMS_TIMEOUT: "Clinic PMS timing out",
  F09_CLINIC_IMPORT_REJECTED: "Clinic PMS rejected the import",
  F10_DELIVERED_NOT_ACTIONED: "Delivered, but nobody has acted on it",
  F11_RESPONSE_NOT_FILED: "The response was not filed with the GP",
};

/** What each tool call means to someone watching the agent work. */
export const TOOL_LABELS: Record<string, string> = {
  get_referral: "Looking up the referral in both systems",
  find_referrals: "Finding the referral",
  get_referral_timeline: "Reading its timeline",
  get_deliveries: "Checking the deliveries to the clinic",
  get_recipient: "Checking the clinic's account and PMS connection",
  get_return_path: "Checking the responses back to the GP",
  search_logs: "Searching the logs",
  list_failed_referrals: "Listing recent failures",
  search_knowledge: "Searching past tickets and runbooks",
  get_runbook: "Reading the runbook",
};

export const OUTCOME_LABELS: Record<string, string> = {
  tool_limit: "it reached its limit of tool calls",
  time_limit: "it ran out of time",
  token_limit: "it reached its token budget",
  max_tokens: "its answer was cut off",
  refused: "the model declined to answer",
  invalid_output: "its answer could not be read",
  unexpected_stop: "it stopped unexpectedly",
};
