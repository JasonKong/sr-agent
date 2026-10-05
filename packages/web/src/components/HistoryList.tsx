import type { HistoryItem } from "../api/client";
import { CODE_TITLES } from "../labels";

interface Props {
  items: HistoryItem[];
  onOpen: (id: string) => void;
}

function cause(item: HistoryItem): string {
  if (item.outcome !== "answered" && !item.root_cause_code) return "Incomplete";
  if (!item.root_cause_code) return "Not enough evidence";
  // The API may know codes this console does not: show them as they are.
  const titles: Record<string, string | undefined> = CODE_TITLES;
  return titles[item.root_cause_code] ?? item.root_cause_code;
}

export function HistoryList({ items, onOpen }: Props) {
  return (
    <section aria-label="History" className="rounded-lg bg-white p-4 shadow-sm sm:p-6">
      <h2 className="font-medium">Your recent diagnoses</h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-slate-600">No diagnoses yet.</p>
      ) : (
        <ul className="mt-2 divide-y divide-slate-100">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => {
                  onOpen(item.id);
                }}
                className="w-full py-2 text-left hover:bg-slate-50"
              >
                <span className="block truncate text-sm">{item.question}</span>
                <span className="text-xs text-slate-600">
                  {cause(item)} · {new Date(item.created_at).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
