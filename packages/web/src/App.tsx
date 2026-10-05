import { useState } from "react";

import {
  diagnose,
  type DiagnosisRequest,
  type DiagnosisResponse,
  type Progress,
} from "./api/client";
import { AskForm } from "./components/AskForm";
import { DiagnosisView } from "./components/DiagnosisView";
import { ProgressList } from "./components/ProgressList";

export function App() {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress[]>([]);
  const [result, setResult] = useState<DiagnosisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 sm:p-8">
      <header>
        <h1 className="text-2xl font-semibold">SR-Agent</h1>
        <p className="text-slate-600">
          Why did a referral fail or not arrive? Ask in your own words; every answer cites the
          records it relied on.
        </p>
      </header>
      <main className="space-y-4">
        <AskForm busy={busy} onSubmit={(r) => void ask(r)} />
        {error && (
          <p role="alert" className="rounded bg-red-50 p-3 text-red-900">
            Something went wrong: {error}
          </p>
        )}
        <ProgressList items={progress} busy={busy} />
        {result && <DiagnosisView result={result} />}
      </main>
    </div>
  );
}
