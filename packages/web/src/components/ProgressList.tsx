import type { Progress } from "../api/client";
import { TOOL_LABELS } from "../labels";

/** The text arguments of a tool call (ids, names), leaving out settings such as limits. */
function ids(args: Record<string, unknown>): string[] {
  return Object.values(args).filter((v): v is string => typeof v === "string");
}

export function ProgressList({ items, busy }: { items: Progress[]; busy: boolean }) {
  const calls = items.flatMap((p) => (p.kind === "tool_call" ? [p.call] : []));
  if (!busy && calls.length === 0) return null;
  return (
    <section aria-label="Progress" className="rounded-lg bg-white p-4 shadow-sm sm:p-6">
      <h2 className="font-medium">{busy ? "Working on it…" : "What the agent checked"}</h2>
      <ol aria-live="polite" className="mt-2 space-y-1 text-sm">
        {calls.map((call, n) => (
          <li key={n} className={call.is_error ? "text-amber-800" : "text-slate-700"}>
            {TOOL_LABELS[call.name] ?? call.name}
            {ids(call.arguments).length > 0 && (
              <span className="whitespace-nowrap text-slate-500">
                {" "}
                ({ids(call.arguments).join(", ")})
              </span>
            )}
            {call.is_error && " — not found or not allowed"}
          </li>
        ))}
        {busy && <li className="text-slate-500">Thinking…</li>}
      </ol>
    </section>
  );
}
