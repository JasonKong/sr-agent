import { useCallback, useEffect, useState } from "react";

import {
  diagnose,
  getDiagnosis,
  getHistory,
  sendFeedback,
  type DiagnosisRequest,
  type DiagnosisResponse,
  type HistoryItem,
  type Progress,
} from "./api/client";
import { AskForm } from "./components/AskForm";
import { DiagnosisView } from "./components/DiagnosisView";
import { FeedbackPanel } from "./components/FeedbackPanel";
import { HistoryList } from "./components/HistoryList";
import { ProgressList } from "./components/ProgressList";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function App() {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress[]>([]);
  const [result, setResult] = useState<DiagnosisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);

  const refresh = useCallback(() => {
    getHistory(20).then(setHistory, (e: unknown) => {
      setError(`could not load your history: ${message(e)}`);
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function ask(request: DiagnosisRequest) {
    setBusy(true);
    setProgress([]);
    setResult(null);
    setError(null);
    try {
      setResult(
        await diagnose(request, (p) => {
          setProgress((items) => [...items, p]);
        }),
      );
      refresh();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setError(null);
    setProgress([]);
    try {
      setResult(await getDiagnosis(id));
    } catch (e) {
      setError(message(e));
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-8">
      <header className="mb-4">
        <h1 className="text-2xl font-semibold">SR-Agent</h1>
        <p className="text-slate-600">
          Why did a referral fail or not arrive? Ask in your own words; every answer cites the
          records it relied on.
        </p>
      </header>
      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <main className="space-y-4">
          <AskForm busy={busy} onSubmit={(r) => void ask(r)} />
          {error && (
            <p role="alert" className="rounded bg-red-50 p-3 text-red-900">
              Something went wrong: {error}
            </p>
          )}
          <ProgressList items={progress} busy={busy} />
          {result && (
            <DiagnosisView result={result}>
              <FeedbackPanel key={result.id} diagnosisId={result.id} send={sendFeedback} />
            </DiagnosisView>
          )}
        </main>
        <aside>
          <HistoryList items={history} onOpen={(id) => void open(id)} />
        </aside>
      </div>
    </div>
  );
}
