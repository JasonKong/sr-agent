import { useState } from "react";

import type { DiagnosisResponse } from "../api/client";
import { CODE_TITLES, OUTCOME_LABELS } from "../labels";

const CONFIDENCE = { high: "High confidence", medium: "Medium confidence", low: "Low confidence" };

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
        });
      }}
      className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
    >
      {copied ? "Copied" : label}
    </button>
  );
}

export function DiagnosisView({ result }: { result: DiagnosisResponse }) {
  const d = result.diagnosis;
  const why = OUTCOME_LABELS[result.outcome] ?? result.outcome;
  return (
    <article aria-label="Diagnosis" className="space-y-4 rounded-lg bg-white p-4 shadow-sm sm:p-6">
      {result.incomplete && (
        <p role="alert" className="rounded bg-amber-50 p-3 text-amber-900">
          {d
            ? `Incomplete: ${why}, so this is a partial answer from the evidence so far.`
            : `No answer: ${why}.`}
        </p>
      )}
      {d && (
        <>
          <p className="text-lg">{d.summary}</p>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-blue-100 px-3 py-1 font-medium text-blue-900">
              {d.root_cause_code ? CODE_TITLES[d.root_cause_code] : "Not enough evidence"}
            </span>
            {d.root_cause_code && (
              <code className="text-sm text-slate-600">{d.root_cause_code}</code>
            )}
            <span className="text-sm text-slate-700">{CONFIDENCE[d.confidence]}</span>
          </div>
          <section>
            <h2 className="font-medium">Next step</h2>
            <p>{d.recommended_action}</p>
            {d.escalate_to_engineering && (
              <p className="mt-1 font-medium text-red-800">Escalate to engineering.</p>
            )}
          </section>
          <details
            role="group"
            aria-label={`Evidence (${d.evidence.length} records)`}
            className="rounded border border-slate-200 p-3"
          >
            <summary className="cursor-pointer font-medium">
              Evidence ({d.evidence.length} records)
            </summary>
            <ul className="mt-2 space-y-2">
              {d.evidence.map((e) => (
                <li key={e.record_id} className="text-sm">
                  <code className="rounded bg-slate-100 px-1">{e.record_id}</code>{" "}
                  <span className="text-slate-500">
                    {e.record_type.replace(/_/g, " ")} · {e.system}
                  </span>
                  <p className="text-slate-700">{e.detail}</p>
                </li>
              ))}
            </ul>
          </details>
          {d.draft_customer_reply && (
            <section className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-medium">Draft reply</h2>
                <CopyButton text={d.draft_customer_reply} label="Copy draft reply" />
              </div>
              <p className="whitespace-pre-wrap rounded bg-slate-50 p-3">
                {d.draft_customer_reply}
              </p>
            </section>
          )}
          {result.safety_flags.length > 0 && (
            <p className="text-sm text-amber-800">
              Part of the answer was removed because it read as clinical advice.
            </p>
          )}
        </>
      )}
      <p className="text-xs text-slate-500">
        {result.tool_calls.length} tool calls · {(result.duration_ms / 1000).toFixed(1)} s
        {result.cost_usd !== null && ` · $${result.cost_usd.toFixed(3)}`} · {result.model}
      </p>
    </article>
  );
}
