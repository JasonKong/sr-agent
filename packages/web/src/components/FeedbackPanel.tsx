import { useState } from "react";

import type { FailureCode, Feedback } from "../api/client";
import { CODE_TITLES } from "../labels";

interface Props {
  diagnosisId: string;
  send: (id: string, feedback: Feedback) => Promise<void>;
}

const CODES = Object.keys(CODE_TITLES) as FailureCode[];

export function FeedbackPanel({ diagnosisId, send }: Props) {
  const [state, setState] = useState<"ask" | "correct" | "sending" | "sent" | "failed">("ask");
  const [code, setCode] = useState<FailureCode | "">("");
  const [comment, setComment] = useState("");

  function submit(feedback: Feedback) {
    setState("sending");
    send(diagnosisId, feedback).then(
      () => {
        setState("sent");
      },
      () => {
        setState("failed");
      },
    );
  }

  if (state === "sent") return <p className="text-sm text-green-800">Thanks for the feedback.</p>;

  return (
    <section aria-label="Feedback" className="space-y-2 border-t border-slate-200 pt-4">
      {state === "failed" && (
        <p role="alert" className="text-sm text-red-800">
          We could not send your feedback. Please try again.
        </p>
      )}
      {state !== "correct" ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-slate-700">Was this right?</span>
          <button
            type="button"
            disabled={state === "sending"}
            onClick={() => {
              submit({ rating: "UP" });
            }}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
          >
            <span aria-hidden="true">👍</span> Helpful
          </button>
          <button
            type="button"
            disabled={state === "sending"}
            onClick={() => {
              setState("correct");
            }}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
          >
            <span aria-hidden="true">👎</span> Not right
          </button>
        </div>
      ) : (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit({
              rating: "DOWN",
              ...(code ? { correct_code: code } : {}),
              ...(comment.trim() ? { comment: comment.trim() } : {}),
            });
          }}
        >
          <label htmlFor="correct-code" className="block text-sm font-medium">
            What was the right root cause?
          </label>
          <select
            id="correct-code"
            value={code}
            onChange={(e) => {
              setCode(e.target.value as FailureCode | "");
            }}
            className="w-full rounded border border-slate-300 p-2 sm:w-auto"
          >
            <option value="">I'm not sure</option>
            {CODES.map((c) => (
              <option key={c} value={c}>
                {CODE_TITLES[c]}
              </option>
            ))}
          </select>
          <label htmlFor="comment" className="block text-sm font-medium">
            Comment <span className="font-normal text-slate-600">(optional)</span>
          </label>
          <textarea
            id="comment"
            value={comment}
            maxLength={2000}
            rows={2}
            onChange={(e) => {
              setComment(e.target.value);
            }}
            className="w-full rounded border border-slate-300 p-2"
          />
          <button
            type="submit"
            className="rounded bg-blue-700 px-3 py-1 text-sm font-medium text-white hover:bg-blue-800"
          >
            Send feedback
          </button>
        </form>
      )}
    </section>
  );
}
