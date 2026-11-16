// 시스템 화면 "백업·유지보수" 탭: 저장된 자료, 내보내기, 웹 복원(합치기), 초안 정리.
// System.tsx에서 떼어 낸 파일이다 (다른 탭과 따로 고칠 수 있게).
import type { DuplicatePolicy, Maintenance, MergeReport, RestoreJob } from "@career-brain/client";
import { Badge, Button, Card, ConfirmDialog, EmptyState, Input, Label, Section, Select, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorText, Loaded } from "../common";
import { api, ApiError } from "../../lib/api";
import { formatDateTime, int } from "../../lib/format";
import { bytes, saveBlob, systemErrorText } from "../../lib/systemLabels";

const RESTORE_ERROR: Record<string, string> = {
  too_large: "파일이 너무 큽니다.",
  unsupported_media_type: "파일 형식을 보낼 수 없습니다.",
  empty_archive: "빈 파일입니다.",
  restore_busy: "이 공간에서 이미 복원 작업이 돌고 있습니다. 끝난 뒤 다시 하세요.",
  restore_finished: "이미 끝난 작업입니다. 새 파일로 다시 시작하세요.",
  restore_job_not_found: "작업이 없어졌습니다 (오래되어 정리됨). 파일을 다시 올리세요.",
  too_many_restore_jobs: "진행 중인 복원 작업이 많습니다. 잠시 후 다시 하세요.",
  not_verified: "먼저 확인을 마쳐야 합칠 수 있습니다.",
  archive_changed: "확인한 파일과 다릅니다. 다시 확인하세요.",
  policy_changed: "확인할 때와 겹침 처리 방식이 다릅니다. 다시 확인하세요.",
  encrypted_archive: "암호화한 묶음입니다. age 신원(비밀 키)을 넣으세요.",
  invalid_identity: "age 신원 형식이 아닙니다 (AGE-SECRET-KEY-1…).",
  decrypt_failed: "이 신원으로 풀 수 없습니다.",
  age_unavailable: "서버에 age 도구가 없습니다 (age 명령 또는 pyrage 설치).",
  invalid_deletions: "삭제 기록 파일 형식이 아닙니다.",
  deletions_workspace_mismatch: "삭제 기록이 묶음과 다른 공간의 것입니다.",
  not_an_archive: "내보내기 묶음(tar.gz)이 아닙니다.",
  format_unsupported: "career-brain 내보내기 묶음이 아닙니다.",
  version_mismatch: "이 설치와 버전이 다른 묶음입니다. 같은 버전으로 맞춘 뒤 하세요.",
  schema_mismatch: "표 구성이 이 설치와 다릅니다.",
  hash_mismatch: "묶음이 손상됐습니다 (해시가 다름).",
  count_mismatch: "묶음이 손상됐습니다 (행 수가 다름).",
  missing_member: "묶음에 빠진 파일이 있습니다.",
  unexpected_member: "묶음에 예상하지 못한 파일이 있습니다.",
  duplicates_found: "이미 있는 자료와 겹치는 행이 있어 멈췄습니다 (겹침: 중단).",
  merge_conflict: "넣을 수 없는 행이 있어 아무것도 바꾸지 않았습니다.",
  verify_failed: "넣은 뒤 확인이 맞지 않아 되돌렸습니다.",
  upgrade_failed: "옛 형식 묶음을 바꾸지 못했습니다.",
  cancelled: "작업이 멈췄습니다.",
  internal_error: "서버 오류로 멈췄습니다. 서버 기록을 확인하세요.",
};

const PHASE: Record<string, string> = {
  queued: "시작하는 중",
  read: "묶음 읽는 중",
  decrypt: "암호 푸는 중",
  match: "겹치는 자료 찾는 중",
  insert: "행 넣는 중",
  verify: "해시 확인 중",
  deletions: "삭제 기록 적용 중",
  blobs: "원본 파일 저장 중",
  commit: "마무리 중",
};

const SKIP_REASON: Record<string, string> = {
  still_referenced: "다른 자료가 아직 가리킴",
  not_permitted: "지울 수 없는 표",
  rejected: "규칙이 거부함",
  kept_existing: "원래 있던 자료라 지우지 않음",
  error: "DB 오류",
};

function message(error: unknown) {
  if (error instanceof ApiError && error.code && RESTORE_ERROR[error.code]) return RESTORE_ERROR[error.code]!;
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

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-[13px]">
      <span className="shrink-0 text-muted">{label}</span>
      <span className="min-w-0 text-right break-words">{children}</span>
    </div>
  );
}

const short = (name: string) => name.replace(/^core\./, "");
const running = (job: RestoreJob | undefined) => job?.status === "verifying" || job?.status === "applying";

export function MaintenanceTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["maintenance"], queryFn: api.maintenance });
  const [recipient, setRecipient] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const exportArchive = useMutation({
    mutationFn: () => api.downloadExport(recipient.trim() || undefined),
    onSuccess: (f) => {
      saveBlob(f.blob, f.filename);
      setSaved(`${f.filename} (${bytes(f.blob.size)})${f.sha256 ? ` · sha256 ${f.sha256.slice(0, 12)}…` : ""}`);
      void qc.invalidateQueries({ queryKey: ["maintenance"] });
    },
  });
  const deletions = useMutation({
    mutationFn: api.downloadDeletions,
    onSuccess: (f) => saveBlob(f.blob, f.filename),
  });
  const purge = useMutation({
    mutationFn: api.purgeDrafts,
    onSuccess: () => {
      setConfirmPurge(false);
      void qc.invalidateQueries({ queryKey: ["maintenance"] });
      void qc.invalidateQueries({ queryKey: ["postings"] });
    },
  });
  return (
    <Loaded query={q}>
      {(m) => (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Section title="저장된 자료">
            <Card className="px-3 py-2">
              <Field label="행">{int(m.rows_total)}개</Field>
              <Field label="원본 파일">
                {int(m.blobs.referenced)}개 참조 · 저장소 {m.blobs.files === null ? "—" : `${int(m.blobs.files)}개`} ·{" "}
                {bytes(m.blobs.bytes)}
              </Field>
              <Field label="마지막 내보내기">
                {m.last_export ? `${formatDateTime(m.last_export.created_at)} · ${bytes(m.last_export.archive_bytes)}` : "없음"}
              </Field>
              {m.last_restore ? (
                <Field label={m.last_restore.kind === "merge" ? "마지막 합치기" : "마지막 복원"}>
                  {formatDateTime(m.last_restore.created_at)} · {int(m.last_restore.rows_total)}행
                </Field>
              ) : null}
            </Card>
            <details className="text-[13px]">
              <summary className="cursor-pointer text-muted">표별 행 수</summary>
              <ul className="mt-2 divide-y divide-line rounded-md border border-line">
                {Object.entries(m.tables)
                  .filter(([, n]) => n > 0)
                  .sort((x, y) => y[1] - x[1])
                  .map(([name, n]) => (
                    <li key={name} className="flex justify-between gap-2 px-3 py-1 tabular-nums">
                      <span className="min-w-0 truncate">{short(name)}</span>
                      <span>{int(n)}</span>
                    </li>
                  ))}
              </ul>
              <p className="mt-2 text-xs text-muted">
                묶음에 넣지 않는 것: {Object.keys(m.excluded).map(short).join(", ")} (복원 뒤 다시 만듦)
              </p>
            </details>
          </Section>

          <Section title="내보내기">
            {m.export_available ? (
              <div className="space-y-2">
                <Label htmlFor="age-recipient">age 공개 키 (선택, 넣으면 암호화)</Label>
                <Input
                  id="age-recipient"
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  placeholder="age1…"
                  spellCheck={false}
                  autoComplete="off"
                  className="font-mono text-xs"
                />
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" disabled={exportArchive.isPending} onClick={() => exportArchive.mutate()}>
                    {exportArchive.isPending ? <Spinner /> : <Download size={15} />}
                    {recipient.trim() ? "암호화해 내려받기" : "내려받기"}
                  </Button>
                  <Button disabled={deletions.isPending} onClick={() => deletions.mutate()}>
                    삭제 기록 내려받기
                  </Button>
                </div>
                {saved ? <p className="text-xs text-ok break-all">{saved}</p> : null}
                <Problem error={exportArchive.error ?? deletions.error} />
                <p className="text-xs text-muted">
                  묶음에는 개인 자료가 들어 있습니다. 암호화하지 않았다면 안전한 곳에 보관하세요. 삭제 기록은 작으니 자주 받아 두면 오래된
                  묶음을 복원한 뒤 그 사이 지운 자료를 다시 지웁니다.
                </p>
              </div>
            ) : (
              <EmptyState>원본 저장소가 없어 이 설치에서는 내려받을 수 없습니다. CLI(personal-api export)를 쓰세요.</EmptyState>
            )}
          </Section>

          <Section title="복원 (이 공간에 합치기)" className="lg:col-span-2">
            {m.restore_available ? (
              <RestorePanel m={m} />
            ) : (
              <EmptyState>원본 저장소가 없어 이 설치에서는 올릴 수 없습니다. CLI(personal-api restore --merge)를 쓰세요.</EmptyState>
            )}
          </Section>

          <Section title="초안 정리">
            <Card className="space-y-2 px-3 py-2.5">
              <p className="text-[13px]">
                확정하지 않은 공고 초안 중 {m.draft_retention_days}일 지난 것: <span className="font-medium">{int(m.drafts_purgeable)}개</span>
              </p>
              <Button size="sm" variant="danger" disabled={!m.drafts_purgeable || purge.isPending} onClick={() => setConfirmPurge(true)}>
                지금 정리
              </Button>
              {purge.data ? <p className="text-xs text-ok">{purge.data.deleted}개를 지웠습니다.</p> : null}
              <Problem error={purge.error} />
              {m.auto ? <AutoMaintenanceLine auto={m.auto} /> : null}
            </Card>
          </Section>
          <ConfirmDialog
            open={confirmPurge}
            onOpenChange={setConfirmPurge}
            title="공고 초안을 지울까요?"
            description={`${m.draft_retention_days}일 넘게 확정하지 않은 초안 ${m.drafts_purgeable}개를 지웁니다. 되돌릴 수 없습니다 (삭제 기록에는 남습니다).`}
            confirmLabel="지우기"
            cancelLabel="닫기"
            tone="danger"
            busy={purge.isPending}
            onConfirm={() => purge.mutate()}
          />
        </div>
      )}
    </Loaded>
  );
}

// ---------------------------------------------------------------- 웹 복원

function RestorePanel({ m }: { m: Maintenance }) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const deletionInput = useRef<HTMLInputElement>(null);
  const sent = useRef<{ archive: File | null; deletions: File | null }>({ archive: null, deletions: null });
  const [file, setFile] = useState<File | null>(null);
  const [deletionLog, setDeletionLog] = useState<File | null>(null);
  const [policy, setPolicy] = useState<DuplicatePolicy>("skip");
  const [identity, setIdentity] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const key = ["restore-job", jobId] as const;
  const job = useQuery({
    queryKey: key,
    queryFn: () => api.restoreJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (running(query.state.data) ? 1000 : false),
  });
  const current = job.data;
  const encrypted = current?.encrypted ?? file?.name.endsWith(".age") ?? false;
  const secret = () => (encrypted && identity.trim() ? identity : undefined);

  useEffect(() => {
    if (current?.status === "done") {
      setIdentity(""); // 비밀 키는 끝나면 화면에서도 지운다
      void qc.invalidateQueries({ queryKey: ["maintenance"] });
    }
  }, [current?.status, qc]);

  const setJob = (j: RestoreJob) => {
    setJobId(j.id);
    qc.setQueryData(["restore-job", j.id], j);
  };
  const check = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("파일을 고르세요.");
      let j = current;
      // 고른 파일이 올린 것과 다르면 새로 올린다 (실패한 작업은 같은 파일로 다시 확인할 수 있다)
      if (!j || j.status === "done" || sent.current.archive !== file) {
        if (jobId) await api.discardRestore(jobId).catch(() => undefined);
        j = await api.uploadRestore(file, file.name);
        sent.current = { archive: file, deletions: null };
        setJob(j);
      }
      if (deletionLog && sent.current.deletions !== deletionLog) {
        j = await api.uploadRestoreDeletions(j.id, deletionLog);
        sent.current.deletions = deletionLog;
        setJob(j);
      }
      if (j.encrypted && !identity.trim()) {
        setJob(j);
        throw new ApiError(400, "encrypted_archive", null);
      }
      return api.verifyRestore(j.id, { on_duplicate: policy, identity: j.encrypted ? identity : undefined });
    },
    onSuccess: setJob,
  });
  const apply = useMutation({
    mutationFn: () =>
      api.applyRestore(current!.id, { on_duplicate: policy, archive_sha256: current!.sha256, identity: secret() }),
    onSuccess: (j) => {
      setConfirm(false);
      setJob(j);
    },
  });
  const reset = useMutation({
    mutationFn: async () => {
      if (jobId && !running(current)) await api.discardRestore(jobId).catch(() => undefined);
    },
    onSettled: () => {
      setJobId(null);
      setFile(null);
      setDeletionLog(null);
      setIdentity("");
      sent.current = { archive: null, deletions: null };
      check.reset();
      apply.reset();
      if (fileInput.current) fileInput.current.value = "";
      if (deletionInput.current) deletionInput.current.value = "";
    },
  });

  const busy = running(current) || check.isPending || apply.isPending;
  const locked = Boolean(current && current.status !== "failed");
  const report = current?.report ?? null;

  return (
    <div className="space-y-3">
      <p className="text-[13px] text-muted">
        내보내기 묶음(.tar.gz, 암호화했다면 .age)을 올려 <span className="text-fg">지금 공간에 합칩니다</span>. 이미 있는 자료는 고치지 않고, 없는
        행만 새 ID로 더합니다. 먼저 확인(아무것도 쓰지 않음)으로 무엇이 들어갈지 보고 합칩니다.
      </p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="restore-file">묶음 파일 (최대 {bytes(m.max_restore_bytes)})</Label>
          <input
            id="restore-file"
            ref={fileInput}
            type="file"
            accept=".gz,.tgz,.age,application/gzip"
            disabled={locked || busy}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full min-w-0 text-[13px] file:mr-3 file:rounded-md file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-[13px]"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="restore-policy">이미 있는 자료와 겹치면</Label>
          <Select
            id="restore-policy"
            value={policy}
            disabled={locked || busy}
            onChange={(e) => setPolicy(e.target.value as DuplicatePolicy)}
          >
            <option value="skip">건너뛰기 (있는 것 그대로)</option>
            <option value="fail">멈추기 (하나라도 겹치면 안 함)</option>
          </Select>
        </div>
        <div className="space-y-1 md:col-span-2">
          <Label htmlFor="restore-deletions">삭제 기록 (선택, 묶음보다 나중에 받은 .jsonl)</Label>
          <input
            id="restore-deletions"
            ref={deletionInput}
            type="file"
            accept=".jsonl,application/x-ndjson"
            disabled={locked || busy}
            onChange={(e) => setDeletionLog(e.target.files?.[0] ?? null)}
            className="block w-full min-w-0 text-[13px] file:mr-3 file:rounded-md file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-[13px]"
          />
        </div>
        {encrypted ? (
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="restore-identity">age 신원 (비밀 키, 저장하지 않음)</Label>
            <Textarea
              id="restore-identity"
              value={identity}
              onChange={(e) => setIdentity(e.target.value)}
              placeholder="AGE-SECRET-KEY-1…"
              rows={2}
              spellCheck={false}
              autoComplete="off"
              disabled={busy}
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted">확인·합치기 요청에만 실어 보내고, 서버는 그 작업 동안만 임시 파일로 둔 뒤 지웁니다.</p>
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2">
        {current?.status !== "verified" && current?.status !== "done" ? (
          <Button variant="primary" disabled={!file || busy} onClick={() => check.mutate()}>
            {check.isPending || current?.status === "verifying" ? <Spinner /> : <Upload size={15} />}
            {current?.status === "failed" || current?.status === "uploaded" ? "다시 확인" : "올려서 확인"}
          </Button>
        ) : null}
        {current?.status === "verified" && report ? (
          <Button variant="primary" disabled={busy || report.rows_added === 0} onClick={() => setConfirm(true)}>
            {report.rows_added === 0 ? "더할 행 없음" : `${int(report.rows_added)}행 합치기`}
          </Button>
        ) : null}
        {jobId || file ? (
          <Button disabled={running(current) || reset.isPending} onClick={() => reset.mutate()}>
            {current?.status === "done" ? "새로 하기" : "취소"}
          </Button>
        ) : null}
      </div>

      {current ? <JobStatus job={current} /> : null}
      <Problem error={check.error ?? apply.error ?? job.error} />
      {report ? <ReportView report={report} /> : null}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="이 공간에 합칠까요?"
        description={
          report
            ? `${report.source_workspace_name}의 묶음에서 ${int(report.rows_added)}행과 원본 ${int(report.blobs)}개를 더합니다. 기존 자료는 바뀌지 않습니다. 더한 행은 하나씩 지워야 되돌릴 수 있습니다.`
            : ""
        }
        confirmLabel="합치기"
        cancelLabel="닫기"
        busy={apply.isPending}
        onConfirm={() => apply.mutate()}
      />
    </div>
  );
}

function JobStatus({ job }: { job: RestoreJob }) {
  const pct = job.total > 0 ? Math.min(100, Math.round((job.done / job.total) * 100)) : null;
  const unit = job.phase === "read" || job.phase === "decrypt" ? bytes : int;
  return (
    <Card className="space-y-1.5 px-3 py-2.5 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge job={job} />
        <span className="min-w-0 truncate text-muted">
          {job.filename ?? "묶음"} · {bytes(job.bytes)}
          {job.encrypted ? " · 암호화" : ""}
          {job.deletions ? ` · 삭제 기록 ${int(job.deletions)}건` : ""}
        </span>
      </div>
      {running(job) ? (
        <div>
          <div className="flex justify-between gap-2 text-xs text-muted">
            <span>{PHASE[job.phase ?? ""] ?? job.phase ?? "진행 중"}</span>
            {job.total > 0 ? (
              <span className="tabular-nums">
                {unit(job.done)} / {unit(job.total)}
              </span>
            ) : null}
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded bg-subtle" role="progressbar" aria-valuenow={pct ?? undefined}>
            <div className="h-full bg-accent transition-all" style={{ width: `${pct ?? 15}%` }} />
          </div>
        </div>
      ) : null}
      {job.status === "failed" && job.error ? (
        <p role="alert" className="text-danger break-words">
          {RESTORE_ERROR[job.error] ?? job.error}
          {job.error_detail ? <span className="block text-xs text-muted">{job.error_detail}</span> : null}
        </p>
      ) : null}
      <p className="font-mono text-[11px] break-all text-muted">sha256 {job.sha256}</p>
    </Card>
  );
}

function StatusBadge({ job }: { job: RestoreJob }) {
  switch (job.status) {
    case "uploaded":
      return <Badge tone="none">올림</Badge>;
    case "verifying":
      return <Badge tone="accent">확인 중</Badge>;
    case "verified":
      return <Badge tone="ok">확인됨 · 아직 쓰지 않음</Badge>;
    case "applying":
      return <Badge tone="accent">합치는 중</Badge>;
    case "done":
      return <Badge tone="ok">합침</Badge>;
    case "failed":
      return <Badge tone="danger">실패</Badge>;
  }
}

function ReportView({ report }: { report: MergeReport }) {
  const tables = report.tables.filter((t) => t.archive_rows > 0 && !(t.name in report.not_merged));
  return (
    <div className="space-y-2">
      <Card className="px-3 py-2">
        <Field label="묶음">
          {report.source_workspace_name}
          {report.source_workspace_id === report.workspace_id ? " (이 공간의 예전 묶음)" : ""}
        </Field>
        <Field label={report.dry_run ? "더할 행" : "더한 행"}>{int(report.rows_added)}개</Field>
        <Field label="겹쳐서 건너뜀">{int(report.rows_skipped)}개</Field>
        <Field label="원본 파일">{int(report.blobs)}개</Field>
        {report.actors_mapped ? <Field label="작성자">묶음의 {report.actors_mapped}명 → 나</Field> : null}
        {report.deletions_applied || report.deletions_skipped ? (
          <Field label="삭제 기록">
            {int(report.deletions_applied)}건 적용 · {int(report.deletions_skipped)}건 건너뜀
          </Field>
        ) : null}
        <Field label="확인">
          <Badge tone="ok">기존 행 그대로 · 더한 행 해시 일치</Badge>
        </Field>
        {report.format_upgraded_from !== null ? (
          <Field label="형식">옛 형식 {report.format_upgraded_from} → 지금 형식으로 바꿔 읽음</Field>
        ) : null}
      </Card>
      {report.notes.map((n) => (
        <p key={n} className="text-xs text-warn">
          {n}
        </p>
      ))}
      {report.skipped_deletions.length ? (
        <details className="text-[13px]" open>
          <summary className="cursor-pointer text-muted">적용하지 않은 삭제 기록 {report.skipped_deletions.length}건</summary>
          <ul className="mt-2 divide-y divide-line rounded-md border border-line">
            {report.skipped_deletions.map((d) => (
              <li key={`${d.target_table}:${d.target_id}`} className="px-3 py-1.5">
                <div className="flex flex-wrap justify-between gap-x-2">
                  <span>{short(d.target_table)}</span>
                  <span className="text-muted">{SKIP_REASON[d.reason] ?? d.reason}</span>
                </div>
                <div className="font-mono text-[11px] break-all text-muted">
                  {d.target_id}
                  {d.detail ? ` · ${d.detail}` : ""}
                </div>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details className="text-[13px]">
        <summary className="cursor-pointer text-muted">표별 결과</summary>
        <ul className="mt-2 divide-y divide-line rounded-md border border-line">
          {tables.map((t) => (
            <li key={t.name} className="px-3 py-1.5">
              <div className="flex justify-between gap-2 tabular-nums">
                <span className="min-w-0 truncate">{short(t.name)}</span>
                <span className="shrink-0">
                  +{int(t.added)}
                  {t.skipped ? <span className="text-muted"> · 건너뜀 {int(t.skipped)}</span> : null}
                </span>
              </div>
              {Object.keys(t.skipped_by).length ? (
                <div className="truncate text-[11px] text-muted">
                  {Object.entries(t.skipped_by)
                    .map(([k, n]) => `${k === "id" ? "같은 ID" : k === "content" ? "같은 내용" : k} ${n}`)
                    .join(" · ")}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted">
          넣지 않는 표: {Object.keys(report.not_merged).map(short).join(", ")} (공간·구성원은 지금 그대로, 백업·삭제 기록은 이 공간 것만)
        </p>
      </details>
    </div>
  );
}

/** 자동 정리(주기 유지보수) 상태: 마지막·다음 실행과 이 공간에서 한 일. */
function AutoMaintenanceLine({ auto }: { auto: NonNullable<Maintenance["auto"]> }) {
  if (!auto.interval_seconds) {
    return <p className="text-xs text-muted">자동 정리: 꺼짐 (CAREER_MAINTENANCE_INTERVAL_SECONDS=0)</p>;
  }
  const done = Object.entries(auto.counts).filter(([, n]) => n > 0);
  return (
    <p className="text-xs text-muted">
      자동 정리: {auto.running ? "실행 중" : auto.last_finished_at ? `마지막 ${formatDateTime(auto.last_finished_at)}` : "아직 안 돎"}
      {auto.next_at ? ` · 다음 ${formatDateTime(auto.next_at)}` : ""}
      {done.length ? ` · 이 공간: ${done.map(([k, n]) => `${k} ${int(n)}`).join(", ")}` : ""}
      {auto.errors ? ` · 오류 ${int(auto.errors)}` : ""}
    </p>
  );
}
