import { createClient, type CoreHealth } from "@career-brain/client";
import { EvidenceCard } from "@career-brain/ui";
import { useEffect, useState } from "react";

const client = createClient({ baseUrl: "/api" });

export function App() {
  const [health, setHealth] = useState<CoreHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    client.coreHealth().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 720, margin: "40px auto", padding: "0 16px" }}>
      <h1>Career Brain — 개인용</h1>
      <p>API: {health ? `ok (core ${health.core_version})` : (error ?? "확인 중…")}</p>
      <EvidenceCard title="예시 경험 카드" status="self_confirmed" sourceLabel="합성 데이터" />
    </main>
  );
}
