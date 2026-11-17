import type { Approval, ApprovalChange, ApprovalStatus, RunStatus } from "@career-brain/client";
import {
  Badge,
  Button,
  Chip,
  ConfirmDialog,
  FilterChips,
  Label,
  Section,
  Select,
  Sheet,
  Spinner,
  Textarea,
} from "@career-brain/ui";
import type { BadgeTone } from "@career-brain/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { ErrorText, errorText, Loaded } from "../common";
import { LiveProvider, useLiveConnected } from "../system/live";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/dates";
import { int, usd } from "../../lib/format";
import {
  ACTION_LABEL,
  APPROVAL_STATUS,
  changeField,
  changeValue,
  INVALID_REASON,
  RUN_CODE,
  RUN_EVENT_KIND,
  RUN_STATUS,
  systemErrorText,
  WORKFLOW_LABEL,
} from "../../lib/systemLabels";

const ACTIVE: RunStatus[] = ["queued", "running", "waiting_approval", "interrupted"];

function message(error: unknown) {
  return systemErrorText(error) ?? errorText(error);
}

function Problem({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="text-[13px] text-danger">
      {message(error)}
    </p>
  );
}

/** 사용자가 손댈 상태(실패·대기·멈춤)만 배지로, 나머지는 옅은 글자로. */
function StatusMark({ status }: { status: { label: string; tone: BadgeTone } }) {
  if (status.tone === "danger" || status.tone === "warn") return <Badge tone={status.tone}>{status.label}</Badge>;
  return <span className="text-[13px] text-muted">{status.label}</span>;
}

function Rows({ children }: { children: ReactNode }) {
  return <ul className="divide-y divide-line border-t border-line">{children}</ul>;
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

function RowButton({
  onClick,
  title,
  meta,
  right,
}: {
  onClick: () => void;
  title: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="-mx-2 flex w-[calc(100%+1rem)] items-center gap-4 rounded-md px-2 py-3 text-left transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{title}</div>
          {meta ? <div className="truncate text-[13px] text-muted tabular-nums">{meta}</div> : null}
        </div>
        {right ? <div className="shrink-0">{right}</div> : null}
      </button>
    </li>
  );
}

/** 라벨·값 줄을 얇은 선으로 나눈 목록 */
function Fields({ children }: { children: ReactNode }) {
  return <dl className="divide-y divide-line border-y border-line">{children}</dl>;
}

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2 text-[13px]">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right break-words tabular-nums">{children}</dd>
    </div>
  );
}

// ---------------------------------------------------------------- 작업·승인 탭

/**
 * 작업·승인 탭: 사람이 결정할 승인 요청을 먼저, 그 아래 실행 목록. 상세는 시트로 연다 (?approval=, ?run=).
 * 실행·승인 상태는 SSE(/core/live)로 받고, 끊기면 각 목록이 몇 초 간격으로 다시 읽는다.
 */
export function RunsTab({
  approval,
  run,
  open,
}: {
  approval?: string;
  run?: string;
  open: (target: { approval?: string; run?: string }) => void;
}) {
  return (
    <LiveProvider>
      <div className="space-y-10">
        <Section title="승인 요청">
          <ApprovalsList onOpen={(id) => open({ approval: id })} />
        </Section>
        <Section title="실행">
          <RunsList onOpen={(id) => open({ run: id })} />
        </Section>
        <Section title="MCP 호출 기록">
          <AuditSection />
        </Section>
      </div>
      <RunSheet runId={run ?? null} onClose={() => open({})} onApproval={(id) => open({ approval: id })} />
      <ApprovalSheet approvalId={approval ?? null} onClose={() => open({})} onRun={(id) => open({ run: id })} />
    </LiveProvider>
  );
}

// ---------------------------------------------------------------- 실행

export function RunsList({ onOpen }: { onOpen: (id: string) => void }) {
  const [status, setStatus] = useState<RunStatus | "">("");
  const live = useLiveConnected();
  const q = useQuery({
    queryKey: ["runs", status],
    queryFn: () => api.runs({ status: status || undefined, limit: 50 }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (!live && query.state.data?.some((r) => ACTIVE.includes(r.status)) ? 5000 : false),
  });
  return (
    <div className="space-y-4">
      <Select
        value={status}
        onChange={(e) => setStatus(e.target.value as RunStatus | "")}
        className="w-auto"
        aria-label="상태"
      >
        <option value="">모든 상태</option>
        {(Object.keys(RUN_STATUS) as RunStatus[]).map((s) => (
          <option key={s} value={s}>
            {RUN_STATUS[s].label}
          </option>
        ))}
      </Select>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <Rows>
              {rows.map((r) => (
                <RowButton
                  key={r.id}
                  onClick={() => onOpen(r.id)}
                  title={WORKFLOW_LABEL[r.workflow] ?? r.workflow}
                  meta={[
                    `${r.step}${r.steps_total ? `/${r.steps_total}` : ""}단계`,
                    r.actor_kind === "agent" ? "에이전트" : "직접",
                    formatDateTime(r.created_at),
                    r.error_code ? (RUN_CODE[r.error_code] ?? r.error_code) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  right={<StatusMark status={RUN_STATUS[r.status]} />}
                />
              ))}
            </Rows>
          ) : (
            <Quiet>실행 기록이 없습니다. 에이전트(MCP)나 API가 여러 단계 작업을 시작하면 여기에 보입니다.</Quiet>
          )
        }
      </Loaded>
    </div>
  );
}

export function RunSheet({
  runId,
  onClose,
  onApproval,
}: {
  runId: string | null;
  onClose: () => void;
  onApproval: (id: string) => void;
}) {
  const qc = useQueryClient();
  const live = useLiveConnected();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId!),
    enabled: !!runId,
    refetchInterval: (query) => (!live && query.state.data && ACTIVE.includes(query.state.data.status) ? 4000 : false),
  });
  const events = useQuery({
    queryKey: ["run-events", runId, run.data?.updated_at],
    queryFn: () => api.runEvents(runId!),
    enabled: !!runId,
  });
  const act = useMutation({
    mutationFn: (what: "cancel" | "interrupt" | "resume") =>
      what === "cancel"
        ? api.cancelRun(runId!)
        : what === "interrupt"
          ? api.interruptRun(runId!)
          : api.resumeRun(runId!),
    onSuccess: (r) => {
      qc.setQueryData(["run", runId], r);
      void qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const [confirmCancel, setConfirmCancel] = useState(false);
  const r = run.data;
  return (
    <Sheet open={!!runId} onOpenChange={(o) => (o ? null : onClose())} title="실행" width="lg">
      {run.isPending ? <Spinner /> : null}
      <ErrorText error={run.error} />
      {r ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold">{WORKFLOW_LABEL[r.workflow] ?? r.workflow}</span>
            <StatusMark status={RUN_STATUS[r.status]} />
            {r.stop_requested ? (
              <Badge tone="warn">{r.stop_requested === "cancel" ? "취소 요청됨" : "멈춤 요청됨"}</Badge>
            ) : null}
          </div>
          <Fields>
            <Field label="진행">
              {r.step}
              {r.steps_total ? ` / ${r.steps_total}` : ""}단계
            </Field>
            <Field label="요청">{r.actor_kind === "agent" ? "에이전트" : "사람"}</Field>
            <Field label="비용">
              {usd(r.cost_usd)}
              {r.cost_unknown ? " (단가 미상 포함)" : ""} · 모델 호출 {int(r.model_calls)}회
            </Field>
            <Field label="시작">{formatDateTime(r.started_at ?? r.created_at)}</Field>
            {r.finished_at ? <Field label="끝남">{formatDateTime(r.finished_at)}</Field> : null}
            {r.wait_reason ? (
              <Field label="기다리는 이유">
                {RUN_CODE[r.wait_reason] ?? r.wait_reason}
                {r.retry_at ? ` · ${formatDateTime(r.retry_at)}에 다시` : ""}
              </Field>
            ) : null}
            {r.error_code ? <Field label="실패 사유">{RUN_CODE[r.error_code] ?? r.error_code}</Field> : null}
            {r.resumes ? <Field label="다시 시작">{r.resumes}회</Field> : null}
          </Fields>
          {r.approval_id ? (
            <Button size="sm" onClick={() => onApproval(r.approval_id!)}>
              연결된 승인 요청 보기
            </Button>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {r.status === "queued" || r.status === "running" ? (
              <Button size="sm" disabled={act.isPending} onClick={() => act.mutate("interrupt")}>
                멈춤
              </Button>
            ) : null}
            {r.status === "interrupted" ? (
              <Button size="sm" variant="primary" disabled={act.isPending} onClick={() => act.mutate("resume")}>
                다시 시작
              </Button>
            ) : null}
            {!["done", "failed", "cancelled"].includes(r.status) ? (
              <Button size="sm" variant="danger" disabled={act.isPending} onClick={() => setConfirmCancel(true)}>
                취소
              </Button>
            ) : null}
          </div>
          <Problem error={act.error} />
          <Section title="이벤트">
            {events.data?.length ? (
              <ol className="divide-y divide-line border-t border-line">
                {events.data.map((e) => (
                  <li key={e.seq} className="py-2 text-[13px]">
                    <span className="text-muted tabular-nums">{formatDateTime(e.created_at)}</span>{" "}
                    {e.code ? (RUN_CODE[e.code] ?? e.code) : (RUN_EVENT_KIND[e.kind] ?? e.kind)}
                    {e.step !== null ? <span className="text-muted"> · {e.step}단계</span> : null}
                    {e.status ? (
                      <span className="text-muted"> · {RUN_STATUS[e.status as RunStatus]?.label ?? e.status}</span>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : events.isPending ? (
              <Spinner />
            ) : (
              <Quiet>이벤트가 없습니다.</Quiet>
            )}
          </Section>
        </div>
      ) : null}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="실행을 취소할까요?"
        description="실행 중이면 이번 단계가 끝난 뒤 멈춥니다. 취소한 실행은 다시 시작할 수 없습니다."
        confirmLabel="취소하기"
        cancelLabel="닫기"
        tone="danger"
        busy={act.isPending}
        onConfirm={() => act.mutate("cancel", { onSettled: () => setConfirmCancel(false) })}
      />
    </Sheet>
  );
}

// ---------------------------------------------------------------- 승인

function approvalTitle(a: Approval) {
  const t = typeof a.summary.title === "string" ? a.summary.title : null;
  return `${ACTION_LABEL[a.action] ?? a.action}${t ? ` · ${t}` : ""}`;
}

export function ApprovalsList({ onOpen }: { onOpen: (id: string) => void }) {
  const [status, setStatus] = useState<ApprovalStatus>("pending");
  const q = useQuery({
    queryKey: ["approvals", status],
    queryFn: () => api.approvals({ status, limit: 50 }),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="space-y-4">
      <Select
        value={status}
        onChange={(e) => setStatus(e.target.value as ApprovalStatus)}
        className="w-auto"
        aria-label="상태"
      >
        {(Object.keys(APPROVAL_STATUS) as ApprovalStatus[]).map((s) => (
          <option key={s} value={s}>
            {APPROVAL_STATUS[s].label}
          </option>
        ))}
      </Select>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <Rows>
              {rows.map((a) => (
                <RowButton
                  key={a.id}
                  onClick={() => onOpen(a.id)}
                  title={approvalTitle(a)}
                  meta={[
                    a.requested_via === "agent" ? "에이전트 요청" : "직접 요청",
                    formatDateTime(a.created_at),
                    a.invalid_reason ? (INVALID_REASON[a.invalid_reason] ?? a.invalid_reason) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  right={<StatusMark status={APPROVAL_STATUS[a.status]} />}
                />
              ))}
            </Rows>
          ) : (
            <Quiet>{status === "pending" ? "기다리는 승인 요청이 없습니다. 에이전트가 제출 같은 일을 요청하면 여기서 사람이 결정합니다." : "해당하는 승인이 없습니다."}</Quiet>
          )
        }
      </Loaded>
    </div>
  );
}

function ChangeRow({ c }: { c: ApprovalChange }) {
  const field = changeField(c.path);
  const doc = c.label ? `${c.label} · ` : "";
  let text: ReactNode;
  if (c.kind === "added") text = c.path.endsWith("]") ? "새로 생김" : `생김: ${changeValue(c.after)}`;
  else if (c.kind === "removed") text = c.path.endsWith("]") ? "빠짐" : `없어짐 (이전: ${changeValue(c.before)})`;
  else
    text = (
      <>
        <span className="text-muted line-through">{changeValue(c.before)}</span> →{" "}
        <span className="font-medium">{changeValue(c.after)}</span>
      </>
    );
  return (
    <li className="py-2 text-[13px]">
      <div className="text-xs text-muted">
        {doc}
        {c.path.endsWith("]") ? "문서" : field}
      </div>
      <div className="break-words">{text}</div>
    </li>
  );
}

function SummaryDocs({ summary }: { summary: Record<string, unknown> }) {
  const docs = Array.isArray(summary.documents) ? (summary.documents as Record<string, unknown>[]) : [];
  if (!docs.length) return null;
  return (
    <ul className="divide-y divide-line border-y border-line">
      {docs.map((d, i) => (
        <li key={String(d.document_id ?? i)} className="flex items-baseline justify-between gap-3 py-2 text-[13px]">
          <span className="min-w-0 truncate">{String(d.title ?? "문서")}</span>
          <span className="shrink-0 text-muted tabular-nums">
            {d.revision ? `${d.revision}번째 버전` : "아직 없음"}
            {d.char_limit ? ` · ${d.char_limit}자 제한` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ApprovalSheet({
  approvalId,
  onClose,
  onRun,
}: {
  approvalId: string | null;
  onClose: () => void;
  onRun: (id: string) => void;
}) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const diff = useQuery({
    queryKey: ["approval-diff", approvalId],
    queryFn: () => api.approvalDiff(approvalId!),
    enabled: !!approvalId,
  });
  const decide = useMutation({
    mutationFn: ({ approve, hash }: { approve: boolean; hash: string }) =>
      (approve ? api.approve : api.reject)(approvalId!, { payload_hash: hash, note: note.trim() || undefined }),
    onSuccess: () => {
      setNote("");
      void qc.invalidateQueries({ queryKey: ["approvals"] });
      void qc.invalidateQueries({ queryKey: ["approval-diff", approvalId] });
      void qc.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: () => void qc.invalidateQueries({ queryKey: ["approval-diff", approvalId] }),
  });
  const d = diff.data;
  const a = d?.approval;
  return (
    <Sheet open={!!approvalId} onOpenChange={(o) => (o ? null : onClose())} title="승인 요청" width="lg">
      {diff.isPending ? <Spinner /> : null}
      <ErrorText error={diff.error} />
      {d && a ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold">{approvalTitle(a)}</span>
            <StatusMark status={APPROVAL_STATUS[a.status]} />
          </div>
          <Fields>
            <Field label="요청">
              {a.requested_via === "agent" ? "에이전트" : "사람"} · {formatDateTime(a.created_at)}
            </Field>
            <Field label="만료">{formatDateTime(a.expires_at)}</Field>
            {a.reason ? <Field label="사유">{a.reason}</Field> : null}
            {a.decided_at ? <Field label="결정">{formatDateTime(a.decided_at)}</Field> : null}
            {a.decision_note ? <Field label="메모">{a.decision_note}</Field> : null}
            {a.used_at ? <Field label="사용">{formatDateTime(a.used_at)}</Field> : null}
            {a.invalid_reason ? (
              <Field label="무효 사유">{INVALID_REASON[a.invalid_reason] ?? a.invalid_reason}</Field>
            ) : null}
            <Field label="내용 해시">
              <code className="text-xs">{a.payload_hash.slice(0, 12)}…</code>
            </Field>
          </Fields>

          <Section title="요청한 내용">
            <SummaryDocs summary={d.requested} />
            {typeof a.payload.note === "string" && a.payload.note ? (
              <p className="text-[13px]">
                <span className="text-muted">메모: </span>
                {a.payload.note}
              </p>
            ) : null}
          </Section>

          <Section title="요청 이후 바뀐 것">
            {d.current === null ? (
              <p className="flex items-start gap-2 rounded-md bg-danger-soft px-3 py-2 text-[13px] text-danger">
                <AlertTriangle size={15} className="mt-0.5 shrink-0" /> 대상이 지워졌습니다.
              </p>
            ) : d.changes.length ? (
              <ul className="divide-y divide-line border-y border-line">
                {d.changes.map((c) => (
                  <ChangeRow key={`${c.path}-${c.kind}`} c={c} />
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted">바뀐 것이 없습니다. 요청한 그대로입니다.</p>
            )}
            {d.target_changed && a.status === "pending" ? (
              <p className="text-[13px] font-medium">대상이 바뀌어 이 요청은 승인할 수 없습니다. 다시 요청하세요.</p>
            ) : null}
          </Section>

          {a.status === "pending" ? (
            <div className="space-y-2">
              <Label htmlFor="approval-note">메모 (선택)</Label>
              <Textarea id="approval-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  disabled={decide.isPending || d.target_changed}
                  onClick={() => decide.mutate({ approve: true, hash: a.payload_hash })}
                >
                  승인
                </Button>
                <Button
                  variant="danger"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ approve: false, hash: a.payload_hash })}
                >
                  거절
                </Button>
              </div>
              <p className="text-xs text-muted">
                승인은 이 내용(해시)과 지금 대상 버전에만 쓰입니다. 대상이 바뀌면 바로 무효가 됩니다.
              </p>
            </div>
          ) : null}
          <Problem error={decide.error} />
          {a.run_id ? (
            <Button size="sm" onClick={() => onRun(a.run_id!)}>
              연결된 실행 보기
            </Button>
          ) : null}
        </div>
      ) : null}
    </Sheet>
  );
}

// ---------------------------------------------------------------- MCP 기록

export function AuditSection() {
  const [status, setStatus] = useState<"" | "ok" | "error">("");
  const [tool, setTool] = useState("");
  const q = useQuery({
    queryKey: ["audit", status, tool],
    queryFn: () => api.audit({ limit: 100, status: status || undefined, tool: tool || undefined }),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="space-y-4">
      <p className="text-[13px] text-muted">
        MCP 도구 호출마다 도구·결과·걸린 시간·인자 해시만 남깁니다. 인자 원문은 저장하지 않습니다.
      </p>
      <Loaded query={q}>
        {(page) => (
          <div className={q.isFetching ? "space-y-6 opacity-70" : "space-y-6"}>
            <p className="text-[13px] text-muted">
              {page.retention_days
                ? `보존 기간 ${page.retention_days}일: 더 오래된 기록은 정기 유지보수가 삭제합니다.`
                : "보존 기간 없음: 기록을 삭제하지 않습니다."}
            </p>
            {page.last_24h.length ? (
              <Section title="지난 24시간">
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="도구 필터">
                  {page.last_24h.map((t) => (
                    <Chip
                      key={t.tool}
                      pressed={tool === t.tool}
                      onClick={() => setTool(tool === t.tool ? "" : t.tool)}
                      count={t.calls}
                    >
                      <span className="font-mono text-xs">{t.tool}</span>
                      {t.errors ? (
                        <span className={tool === t.tool ? "text-bg" : "text-danger"}>오류 {int(t.errors)}</span>
                      ) : null}
                    </Chip>
                  ))}
                </div>
              </Section>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <FilterChips<"" | "ok" | "error">
                label="결과"
                value={status}
                onChange={setStatus}
                items={[
                  { value: "", label: "모든 결과" },
                  { value: "ok", label: "성공" },
                  { value: "error", label: "오류" },
                ]}
              />
              {tool ? (
                <Button size="sm" variant="ghost" onClick={() => setTool("")}>
                  도구 필터 해제: {tool}
                </Button>
              ) : null}
            </div>
            {page.events.length ? (
              <Rows>
                {page.events.map((e) => (
                  <li key={e.id} className="flex items-center gap-4 py-3 text-[13px]">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-xs">{e.tool ?? e.event}</div>
                      <div className="truncate text-muted tabular-nums">
                        {formatDateTime(e.created_at)} ·{" "}
                        {e.channel === "mcp-stdio" ? "stdio" : e.channel === "mcp-http" ? "원격" : e.channel} ·{" "}
                        {int(e.duration_ms)}ms{e.args_sha256 ? ` · 인자 ${e.args_sha256.slice(0, 8)}` : ""}
                      </div>
                    </div>
                    {e.status === "ok" ? (
                      <span className="shrink-0 text-muted">성공</span>
                    ) : (
                      <Badge tone="danger">{e.error_code ?? "오류"}</Badge>
                    )}
                  </li>
                ))}
              </Rows>
            ) : (
              <Quiet>기록이 없습니다. MCP 클라이언트가 도구를 부르면 여기에 보입니다.</Quiet>
            )}
          </div>
        )}
      </Loaded>
    </div>
  );
}
