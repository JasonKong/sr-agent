import { useState, type SyntheticEvent } from "react";

import type { DiagnosisRequest } from "../api/client";

const REFERRAL_ID = /^(GR|HR)-\d+$/;

interface Props {
  busy: boolean;
  onSubmit: (request: DiagnosisRequest) => void;
}

export function AskForm({ busy, onSubmit }: Props) {
  const [question, setQuestion] = useState("");
  const [referralId, setReferralId] = useState("");
  const id = referralId.trim().toUpperCase();
  const idInvalid = id !== "" && !REFERRAL_ID.test(id);
  const ready = question.trim().length >= 5 && !idInvalid && !busy;

  function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready) return;
    onSubmit(id ? { question: question.trim(), referral_id: id } : { question: question.trim() });
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-lg bg-white p-4 shadow-sm sm:p-6">
      <div>
        <label htmlFor="question" className="block font-medium">
          What did the customer say?
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(e) => {
            setQuestion(e.target.value);
          }}
          rows={3}
          maxLength={2000}
          placeholder="The specialist at Kowhai Orthopaedics says they never got GR-10234."
          className="mt-1 w-full rounded border border-slate-300 p-2 focus:outline-2 focus:outline-blue-600"
        />
      </div>
      <div>
        <label htmlFor="referral" className="block font-medium">
          Referral id <span className="font-normal text-slate-600">(optional)</span>
        </label>
        <input
          id="referral"
          value={referralId}
          onChange={(e) => {
            setReferralId(e.target.value);
          }}
          placeholder="GR-10234"
          aria-invalid={idInvalid}
          aria-describedby={idInvalid ? "referral-error" : undefined}
          className="mt-1 w-full rounded border border-slate-300 p-2 sm:w-64"
        />
        {idInvalid && (
          <p id="referral-error" className="mt-1 text-sm text-red-700">
            A referral id looks like GR-10234 or HR-55210.
          </p>
        )}
      </div>
      <button
        type="submit"
        disabled={!ready}
        className="rounded bg-blue-700 px-4 py-2 font-medium text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-400"
      >
        {busy ? "Diagnosing…" : "Diagnose"}
      </button>
    </form>
  );
}
