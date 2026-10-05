import type { DiagnosisResponse } from "./api/client";

/** A complete API response, for tests; override any field. */
export function diagnosisResponse(overrides: Partial<DiagnosisResponse> = {}): DiagnosisResponse {
  return {
    id: "dx_0123456789ab",
    created_at: "2026-10-05T09:00:00Z",
    question: "The specialist says GR-11854 never arrived in their system.",
    outcome: "answered",
    incomplete: false,
    diagnosis: {
      summary: "The clinic's PMS token expired, so the push into its system failed.",
      root_cause_code: "F07_CLINIC_PMS_AUTH_FAILED",
      confidence: "high",
      evidence: [
        {
          record_type: "delivery",
          record_id: "dl_538183",
          system: "hub",
          detail: "CLINIC_PMS delivery FAILED, no attempts",
        },
        {
          record_type: "log",
          record_id: "hub-worker:009502",
          system: "hub",
          detail: "auth failed: token expired",
        },
      ],
      recommended_action: "Ask the clinic to re-authorise its PMS connection, then resend.",
      draft_customer_reply: "We have found the problem and will resend the referral today.",
      escalate_to_engineering: false,
    },
    model: "claude-opus-5-5",
    served_by: ["claude-opus-5-5"],
    prompt_version: "v1",
    turns: 4,
    tool_calls: [],
    duration_ms: 14200,
    cost_usd: 0.21,
    safety_flags: [],
    dropped_evidence: [],
    feedback: [],
    ...overrides,
  };
}
