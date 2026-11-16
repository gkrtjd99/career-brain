import type { Run, RunEstimate } from "@career-brain/client";
import { Badge } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { int, usd } from "../../lib/format";
import { RUN_CODE, RUN_STATUS } from "../../lib/systemLabels";
import { ErrorText } from "../common";
import { LiveProvider, useLiveConnected } from "../system/live";

// 웹에서 시작하는 일괄 실행(c0023)의 공용 조각: 시작 전 참고 비용, 진행 표시(시스템 화면 실행 상세로 연결).
// 실행 기록에는 글이 없다 (ID·개수만). 결과 글은 각 화면(수정안 탭·인터뷰)에서 본다.

const ACTIVE: Run["status"][] = ["queued", "running", "waiting_approval"];
// 실시간 알림(SSE)이 끊겼을 때만 다시 읽는 간격
const FALLBACK_POLL_MS = 5000;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** 창 하나의 멱등 키 바탕 (보안 문맥이 아니면 randomUUID 가 없을 수 있다) */
export function newRunKey(prefix: string): string {
  const r =
    typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `web:${prefix}:${r}`;
}

/** 바탕 + 입력 해시: 같은 창·같은 입력이면 다시 눌러도 같은 실행, 입력을 바꾸면 새 실행 */
export function runKey(base: string, input: unknown): string {
  const text = JSON.stringify(input);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return `${base}:${h.toString(16)}`;
}

/**
 * 시작 전 참고 비용. 지난 실행이 없으면 호출 수만 보인다. ``input``(시작할 입력 그대로)을 주면 서버가 글자 수를
 * 세어 입력 크기로 잰다 (지난 실행이 모자라면 호출 1회 평균). 어느 근거인지 같이 보인다.
 */
export function CostEstimate({
  workflow,
  units,
  noun,
  input,
}: {
  workflow: string;
  units: number;
  noun: string;
  input?: Record<string, unknown>;
}) {
  // 요청 글을 치는 동안 매 글자 다시 묻지 않는다
  const sent = useDebounced(input ? JSON.stringify(input) : null, 400);
  const q = useQuery({
    queryKey: ["run-estimate", workflow, units, sent],
    queryFn: () =>
      sent ? api.runEstimateFor(workflow, JSON.parse(sent) as Record<string, unknown>) : api.runEstimate(workflow, units),
    enabled: units > 0,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
  if (units <= 0) return null;
  const e: RunEstimate | undefined = q.data;
  return (
    <div className="rounded-md border border-line bg-subtle/60 px-3 py-2 text-[13px]" aria-live="polite">
      <p>
        모델 호출 약 <b>{int(units)}회</b> ({noun} {int(units)}개, 하나에 한 번)
        {e?.chars != null ? <span className="text-muted"> · 보낼 글 약 {int(e.chars)}자</span> : null}
      </p>
      {e?.estimate_usd ? (
        <p className="text-muted">
          예상 비용 약 <b className="text-fg">{usd(e.estimate_usd)}</b> —{" "}
          {e.basis === "input_chars" ? (
            <>
              글 길이 기준: 지난 실행 {int(e.based_on_runs)}번(호출 {int(e.based_on_calls)}회, {int(e.based_on_chars)}자)에서
              잰 호출당 {usd(e.fixed_per_call_usd)} + 1천 자당 {usd(e.per_1k_chars_usd)}
            </>
          ) : (
            <>
              호출 평균 기준: 이 작업의 지난 실행 {int(e.based_on_runs)}번(호출 {int(e.based_on_calls)}회) 평균{" "}
              {usd(e.per_call_usd)}/회
              {e.chars != null ? " (글 길이로 잴 지난 실행이 아직 모자람)" : ""}
            </>
          )}
          {e.cost_unknown ? ", 단가 미상 호출이 있어 실제는 더 들 수 있음" : ""}. 참고값이며 월 예산은 실행 때 다시 확인합니다.
        </p>
      ) : e ? (
        <p className="text-muted">아직 이 작업을 돌린 적이 없어 예상 비용이 없습니다. 끝나면 시스템 화면에서 실제 비용을 봅니다.</p>
      ) : null}
      <ErrorText error={q.error} />
    </div>
  );
}

function progress(run: Run, listKey: string): { done: number; total: number } | null {
  const list = run.position[listKey];
  if (!Array.isArray(list)) return null;
  const next = typeof run.position.next === "number" ? run.position.next : 0;
  return { done: run.status === "done" ? list.length : Math.min(next, list.length), total: list.length };
}

type ProgressProps = {
  runId: string;
  listKey: string;
  noun: string;
  onFinished?: (run: Run) => void;
};

/**
 * 실행 진행. 실행 상태 알림(SSE ``/core/live``)을 받으면 다시 읽고, 알림이 끊겼을 때만 몇 초마다 다시 읽는다.
 * ``listKey``: 위치(position)의 대상 목록 이름
 */
export function RunProgress(props: ProgressProps) {
  return (
    <LiveProvider>
      <RunProgressCard {...props} />
    </LiveProvider>
  );
}

function RunProgressCard({ runId, listKey, noun, onFinished }: ProgressProps) {
  const live = useLiveConnected();
  const q = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (query) =>
      live || (query.state.data && !ACTIVE.includes(query.state.data.status)) ? false : FALLBACK_POLL_MS,
  });
  const run = q.data;
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (run && !ACTIVE.includes(run.status) && reported.current !== run.id) {
      reported.current = run.id;
      onFinished?.(run);
    }
  }, [run, onFinished]);
  if (!run) return <ErrorText error={q.error} />;
  const p = progress(run, listKey);
  const pct = p && p.total ? Math.round((p.done / p.total) * 100) : run.status === "done" ? 100 : 0;
  const st = RUN_STATUS[run.status];
  return (
    <div className="space-y-2 rounded-md border border-line px-3 py-2.5 text-[13px]" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={st.tone}>{st.label}</Badge>
        {p ? (
          <span className="tabular-nums">
            {noun} {int(p.done)}/{int(p.total)}
          </span>
        ) : (
          <span className="text-muted">준비 중</span>
        )}
        <span className="text-muted tabular-nums">
          · {usd(run.cost_usd)}
          {run.cost_unknown ? " (단가 미상 포함)" : ""} · 호출 {int(run.model_calls)}회
        </span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-subtle"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="진행"
      >
        <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
      </div>
      {run.wait_reason ? <p className="text-muted">기다리는 중: {RUN_CODE[run.wait_reason] ?? run.wait_reason}</p> : null}
      {run.error_code ? <p className="text-danger">실패: {RUN_CODE[run.error_code] ?? run.error_code}</p> : null}
      <Link to="/system" search={{ run: run.id }} className="inline-flex items-center gap-1 text-accent hover:underline">
        <ExternalLink size={13} /> 시스템 화면에서 실행 보기 (멈춤·취소·사건)
      </Link>
    </div>
  );
}
