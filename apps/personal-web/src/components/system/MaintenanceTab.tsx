// 연결·관리 화면 "백업·유지보수" 탭: 저장된 자료, 내보내기, 웹 복원(합치기), 초안 정리.
// System.tsx에서 떼어 낸 파일이다 (다른 탭과 따로 고칠 수 있게).
import type {
  DuplicatePolicy,
  Maintenance,
  NearChoice,
  NearDecision,
  NearDuplicate,
  RestoreJob,
} from "@career-brain/client";
import { Badge, Button, ConfirmDialog, Input, Label, Section, Select, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorText, Loaded } from "../common";
import { api, ApiError } from "../../lib/api";
import { formatDateTime } from "../../lib/dates";
import { int } from "../../lib/format";
import { bytes, saveBlob, systemErrorText } from "../../lib/systemLabels";
import {
  currentRestoreJob,
  uploadRestoreWithProgress,
  type MergeReportMore,
  type RestoreJobMore,
  type UploadProgress,
} from "./restoreUpload";

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
  version_mismatch:
    "이 설치가 바꿔 읽을 수 없는 버전의 묶음입니다 (이 설치보다 새 버전이거나, 그 사이 migration의 변환 단계가 없음). 같은 버전으로 맞춘 뒤 하세요.",
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
  interrupted: "서버가 다시 시작되어 작업이 끊겼습니다. 아무것도 바뀌지 않았으니 다시 확인하세요.",
  upload_missing: "올린 파일이 정리되어 없습니다. 파일을 다시 올리세요.",
  upload_failed: "업로드가 끊겼습니다. 연결을 확인하고 다시 하세요.",
  upload_cancelled: "업로드를 멈췄습니다.",
  internal_error: "서버 오류로 멈췄습니다. 서버 기록을 확인하세요.",
  forbidden: "합칠 권한이 없습니다 (소유자만). 역할이 바뀌었는지 확인하세요.",
  near_duplicate_unknown: "확인 보고에 없는 비슷한 행 쌍입니다. 다시 확인하세요.",
  near_duplicate_changed: "확인한 뒤 이 공간의 자료가 바뀌어 고른 비슷한 행이 맞지 않습니다. 다시 확인하고 고르세요.",
};

const PHASE: Record<string, string> = {
  queued: "차례를 기다리는 중. 오래 걸리면 연결·관리 › 작업에서 상태를 보세요",
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
  kept_existing: "원래 있던 자료라 남겨 둠",
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

/** 라벨·값 줄을 얇은 선으로 나눈 목록 */
function Fields({ children }: { children: ReactNode }) {
  return <dl className="divide-y divide-line border-y border-line">{children}</dl>;
}

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 text-[13px]">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right break-words tabular-nums">{children}</dd>
    </div>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

/** 펼쳐 보는 보조 목록 (표별 행 수 등) */
const HAIRLINE_LIST = "mt-2 divide-y divide-line border-y border-line";

const short = (name: string) => name.replace(/^core\./, "");
const running = (job: RestoreJob | RestoreJobMore | null | undefined) =>
  job?.status === "verifying" || job?.status === "applying";
const keyLabel = (k: string) =>
  k === "id"
    ? "같은 ID"
    : k === "content"
      ? "같은 내용"
      : k === "near_duplicate"
        ? "비슷한 행(고른 건너뛰기)"
        : k.startsWith("same_except:")
          ? "같은 내용(상태·메모·시각 제외)"
          : k;
const nearKey = (p: { table: string; incoming_id: string }) => `${p.table}:${p.incoming_id}`;

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
        <div className="max-w-3xl space-y-10">
          <Section title="저장된 자료">
            <Fields>
              <Field label="행">{int(m.rows_total)}개</Field>
              <Field label="원본 파일">
                {int(m.blobs.referenced)}개 참조 · 저장소 {m.blobs.files === null ? "—" : `${int(m.blobs.files)}개`} ·{" "}
                {bytes(m.blobs.bytes)}
              </Field>
              <Field label="마지막 내보내기">
                {m.last_export
                  ? `${formatDateTime(m.last_export.created_at)} · ${bytes(m.last_export.archive_bytes)}`
                  : "없음"}
              </Field>
              {m.last_restore ? (
                <Field label={m.last_restore.kind === "merge" ? "마지막 합치기" : "마지막 복원"}>
                  {formatDateTime(m.last_restore.created_at)} · {int(m.last_restore.rows_total)}행
                </Field>
              ) : null}
            </Fields>
            <details className="text-[13px]">
              <summary className="cursor-pointer py-1 text-muted hover:text-fg">표별 행 수</summary>
              <ul className={HAIRLINE_LIST}>
                {Object.entries(m.tables)
                  .filter(([, n]) => n > 0)
                  .sort((x, y) => y[1] - x[1])
                  .map(([name, n]) => (
                    <li key={name} className="flex justify-between gap-2 py-1.5 tabular-nums">
                      <span className="min-w-0 truncate">{short(name)}</span>
                      <span>{int(n)}</span>
                    </li>
                  ))}
              </ul>
              <p className="mt-2 text-[13px] text-muted">
                묶음에 넣지 않는 것: {Object.keys(m.excluded).map(short).join(", ")} (복원 뒤 다시 만듦)
              </p>
            </details>
          </Section>

          <Section title="내보내기">
            {m.export_available ? (
              <div className="space-y-2 sm:max-w-md">
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
                {saved ? <p className="text-[13px] break-all">내려받음: {saved}</p> : null}
                <Problem error={exportArchive.error ?? deletions.error} />
                <p className="text-[13px] text-muted">
                  묶음에는 개인 자료가 들어 있습니다. 암호화하지 않았다면 안전한 곳에 보관하세요. 삭제 기록은 작으니
                  자주 받아 두면 오래된 묶음을 복원한 뒤 그 사이 지운 자료를 다시 지웁니다.
                </p>
              </div>
            ) : (
              <Quiet>원본 저장소가 없어 이 설치에서는 내려받을 수 없습니다. CLI(personal-api export)를 쓰세요.</Quiet>
            )}
          </Section>

          <MarkdownExport filesAvailable={m.export_available} />

          <Section title="복원 (이 공간에 합치기)">
            {m.restore_available ? (
              <RestorePanel m={m} />
            ) : (
              <Quiet>
                원본 저장소가 없어 이 설치에서는 올릴 수 없습니다. CLI(personal-api restore --merge)를 쓰세요.
              </Quiet>
            )}
          </Section>

          <Section title="초안 정리">
            <div className="flex items-center justify-between gap-4 border-y border-line py-2.5 text-[13px]">
              <span className="min-w-0">
                확정하지 않은 공고 초안 중 {m.draft_retention_days}일 지난 것{" "}
                <span className="font-medium tabular-nums">{int(m.drafts_purgeable)}개</span>
              </span>
              <Button
                size="sm"
                variant="danger"
                disabled={!m.drafts_purgeable || purge.isPending}
                onClick={() => setConfirmPurge(true)}
              >
                지금 정리
              </Button>
            </div>
            {purge.data ? <p className="text-[13px]">{purge.data.deleted}개를 삭제했습니다.</p> : null}
            <Problem error={purge.error} />
            {m.auto ? <AutoMaintenanceLine auto={m.auto} /> : null}
          </Section>
          <ConfirmDialog
            open={confirmPurge}
            onOpenChange={setConfirmPurge}
            title="공고 초안을 지울까요?"
            description={`${m.draft_retention_days}일 넘게 확정하지 않은 초안 ${m.drafts_purgeable}개를 지웁니다. 되돌릴 수 없습니다 (삭제 기록에는 남습니다).`}
            confirmLabel="삭제"
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

// ---------------------------------------------------------------- Markdown으로 내보내기

const MARKDOWN_ERROR: Record<string, string> = {
  too_large: "내보낼 자료가 너무 큽니다. 원본 파일을 빼고 다시 하세요.",
  files_unavailable: "이 설치에는 원본 저장소가 없어 원본 파일을 넣을 수 없습니다.",
};

function Check({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <label className="flex min-h-9 cursor-pointer items-start gap-2.5 py-2 text-[13px]">
      <input
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-fg"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="min-w-0">
        {label}
        <span className="block text-muted">{hint}</span>
      </span>
    </label>
  );
}

/** 노트 앱에서 여는 읽기 전용 묶음. 백업이 아니므로 "마지막 내보내기"에 세지 않는다 */
function MarkdownExport({ filesAvailable }: { filesAvailable: boolean }) {
  const [sensitive, setSensitive] = useState(false);
  const [candidates, setCandidates] = useState(false);
  const [files, setFiles] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const download = useMutation({
    mutationFn: () => api.downloadMarkdown({ sensitive, candidates, files: filesAvailable && files }),
    onSuccess: (f) => {
      saveBlob(f.blob, f.filename);
      setSaved(`${f.filename} (${bytes(f.blob.size)})`);
    },
  });
  const error = download.error;
  const errorMessage =
    error instanceof ApiError && error.code && MARKDOWN_ERROR[error.code]
      ? MARKDOWN_ERROR[error.code]
      : error
        ? message(error)
        : null;
  return (
    <Section title="Markdown으로 내보내기">
      <div className="space-y-2 sm:max-w-md">
        <p className="text-[13px] text-muted">Obsidian 같은 노트 앱에서 열 수 있습니다. 다시 가져올 수는 없습니다.</p>
        <div className="divide-y divide-line border-y border-line">
          <Check
            checked={sensitive}
            onChange={setSensitive}
            label="민감 정보 포함"
            hint="생년월일·주소·병역·보훈·장애·희망 조건과 자격 번호 전체를 넣습니다."
          />
          <Check
            checked={candidates}
            onChange={setCandidates}
            label="확인 전 사실 포함"
            hint="아직 확인하지 않은 후보를 따로 적습니다."
          />
          {filesAvailable ? (
            <Check
              checked={files}
              onChange={setFiles}
              label="원본 파일 포함"
              hint="올린 파일을 함께 넣습니다. 증빙 서류는 넣지 않습니다."
            />
          ) : null}
        </div>
        <Button disabled={download.isPending} onClick={() => download.mutate()}>
          {download.isPending ? <Spinner /> : <Download size={15} />}
          Markdown 내려받기
        </Button>
        {saved ? <p className="text-[13px] break-all">내려받음: {saved}</p> : null}
        {errorMessage ? (
          <p role="alert" className="text-[13px] text-danger">
            {errorMessage}
          </p>
        ) : null}
      </div>
    </Section>
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
  const [upload, setUpload] = useState<UploadProgress | null>(null);
  // 비슷한 행 쌍마다 고른 처리 (없으면 둘 다 둠 = 기본)
  const [near, setNear] = useState<Record<string, NearDecision>>({});
  const abort = useRef<AbortController | null>(null);
  const key = ["restore-job", jobId] as const;
  const job = useQuery({
    queryKey: key,
    queryFn: () => api.restoreJob(jobId!) as Promise<RestoreJobMore>,
    enabled: jobId !== null,
    refetchInterval: (query) => (running(query.state.data) ? 1000 : false),
  });
  // 화면을 다시 열면 이 공간의 지금 작업을 이어 본다 (작업 상태는 서버 DB에 있다)
  const resumed = useQuery({ queryKey: ["restore-current"], queryFn: currentRestoreJob, refetchOnWindowFocus: false });
  useEffect(() => {
    const j = resumed.data;
    if (j && jobId === null) {
      qc.setQueryData(["restore-job", j.id], j);
      setJobId(j.id);
    }
    // 서버에서 받은 작업이 바뀔 때만 (지금 보는 작업이 있으면 바꾸지 않는다)
  }, [resumed.data]);
  const current = job.data;
  const encrypted = current?.encrypted ?? file?.name.endsWith(".age") ?? false;
  const secret = () => (encrypted && identity.trim() ? identity : undefined);

  useEffect(() => {
    if (current?.status === "done") {
      setIdentity(""); // 비밀 키는 끝나면 화면에서도 지운다
      void qc.invalidateQueries({ queryKey: ["maintenance"] });
    }
  }, [current?.status, qc]);

  const setJob = (j: RestoreJob | RestoreJobMore) => {
    setJobId(j.id);
    qc.setQueryData(["restore-job", j.id], j);
  };
  const check = useMutation({
    mutationFn: async () => {
      let j = current;
      // 이어 본 작업(파일을 고르지 않음)은 서버에 남은 파일로 다시 확인한다
      const reuse = !file && j && j.status !== "done";
      if (!file && !reuse) throw new Error("파일을 고르세요.");
      // 고른 파일이 올린 것과 다르면 새로 올린다 (실패한 작업은 같은 파일로 다시 확인할 수 있다)
      if (file && (!j || j.status === "done" || sent.current.archive !== file)) {
        if (jobId) await api.discardRestore(jobId).catch(() => undefined);
        const ctl = new AbortController();
        abort.current = ctl;
        try {
          j = await uploadRestoreWithProgress(file, file.name, setUpload, ctl.signal);
        } finally {
          abort.current = null;
          setUpload(null);
        }
        sent.current = { archive: file, deletions: null };
        setJob(j);
      }
      if (!j) throw new Error("파일을 고르세요.");
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
  const nearChoices = (): NearChoice[] =>
    ((current?.report ?? null) as MergeReportMore | null)?.near_duplicates
      ?.filter((p) => near[nearKey(p)] === "skip_incoming")
      .map((p) => ({
        table: p.table,
        incoming_id: p.incoming_id,
        existing_id: p.existing_id,
        decision: "skip_incoming",
      })) ?? [];
  const apply = useMutation({
    mutationFn: () =>
      api.applyRestore(current!.id, {
        on_duplicate: policy,
        archive_sha256: current!.sha256,
        identity: secret(),
        near_duplicates: nearChoices(),
      }),
    onSuccess: (j) => {
      setConfirm(false);
      setJob(j);
    },
  });
  // 큐에서 아직 기다리는 작업은 취소할 수 있다 (worker가 집기 전)
  const waiting = running(current) && current?.phase === "queued";
  const reset = useMutation({
    mutationFn: async () => {
      if (jobId && (!running(current) || waiting)) await api.discardRestore(jobId).catch(() => undefined);
    },
    onSettled: () => {
      qc.setQueryData(["restore-current"], null); // 지운 작업을 다시 잇지 않게
      setJobId(null);
      setFile(null);
      setDeletionLog(null);
      setIdentity("");
      setNear({});
      sent.current = { archive: null, deletions: null };
      check.reset();
      apply.reset();
      if (fileInput.current) fileInput.current.value = "";
      if (deletionInput.current) deletionInput.current.value = "";
    },
  });

  const busy = running(current) || check.isPending || apply.isPending;
  const locked = Boolean(current && current.status !== "failed");
  const report = (current?.report ?? null) as MergeReportMore | null;

  return (
    <div className="space-y-4">
      <p className="text-[13px] text-muted">
        내보내기 묶음(.tar.gz, 암호화했다면 .age)을 올려 <span className="text-fg">지금 공간에 합칩니다</span>. 이미
        있는 자료는 바꾸지 않고, 없는 행만 새 ID로 더합니다. 먼저 확인(아무것도 쓰지 않음)으로 무엇이 들어갈지 보고
        합칩니다.
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
            <p className="text-[13px] text-muted">
              확인·합치기 요청에만 실어 보내고, 서버는 작업이 쓸 때까지 작업 폴더에 본인만 읽는 파일로 둔 뒤 삭제합니다.
            </p>
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2">
        {current?.status !== "verified" && current?.status !== "done" ? (
          <Button variant="primary" disabled={(!file && !current) || busy} onClick={() => check.mutate()}>
            {check.isPending || current?.status === "verifying" ? <Spinner /> : <Upload size={15} />}
            {current?.status === "failed" || current?.status === "uploaded" ? "다시 확인" : "올려서 확인"}
          </Button>
        ) : null}
        {current?.status === "verified" && report ? (
          <Button variant="primary" disabled={busy || report.rows_added === 0} onClick={() => setConfirm(true)}>
            {report.rows_added === 0 ? "더할 행 없음" : `${int(report.rows_added)}행 합치기`}
          </Button>
        ) : null}
        {upload ? (
          <Button onClick={() => abort.current?.abort()}>업로드 멈추기</Button>
        ) : jobId || file ? (
          <Button disabled={(running(current) && !waiting) || reset.isPending} onClick={() => reset.mutate()}>
            {current?.status === "done" ? "새로 하기" : "취소"}
          </Button>
        ) : null}
      </div>

      {upload ? <UploadBar progress={upload} name={file?.name} /> : null}
      {current && !upload ? <JobStatus job={current} /> : null}
      <Problem error={check.error ?? apply.error ?? job.error} />
      {report?.near_duplicates?.length ? (
        <NearDuplicates
          report={report}
          choices={near}
          editable={current?.status === "verified" && !busy}
          onChange={(key, d) => setNear((prev) => ({ ...prev, [key]: d }))}
        />
      ) : null}
      {report ? <ReportView report={report} /> : null}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="이 공간에 합칠까요?"
        description={
          report
            ? `${report.source_workspace_name}의 묶음에서 ${int(report.rows_added)}행과 원본 ${int(report.blobs)}개를 더합니다.${
                nearChoices().length
                  ? ` 비슷한 행 ${nearChoices().length}쌍은 들어오는 행을 건너뛰고 기존 행으로 이어서 더하는 행이 그만큼(과 그 자식만큼) 줄어듭니다.`
                  : ""
              } 기존 자료는 바뀌지 않습니다. 더한 행은 하나씩 지워야 되돌릴 수 있습니다.`
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

const COLUMN_LABEL: Record<string, string> = {
  title: "제목",
  organization: "조직",
  role: "역할",
  period_text: "기간",
  statement: "문장",
  contribution: "기여",
  question: "문제",
  wrong_answer: "틀린 답",
  correct_answer: "정답",
};

function shown(v: unknown) {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** 정체 칸만 다른 (들어오는 행, 기존 행) 쌍: 쌍마다 "둘 다 두기"(기본) 또는 "들어오는 행 건너뛰기". */
function NearDuplicates({
  report,
  choices,
  editable,
  onChange,
}: {
  report: MergeReportMore;
  choices: Record<string, NearDecision>;
  editable: boolean;
  onChange: (key: string, decision: NearDecision) => void;
}) {
  const pairs: NearDuplicate[] = report.near_duplicates ?? [];
  const total = report.near_duplicates_total ?? pairs.length;
  return (
    <details className="text-[13px]" open>
      <summary className="cursor-pointer py-1 font-medium">
        비슷한 행 {int(total)}쌍 {report.dry_run ? "— 합치기 전에 고르세요" : "— 고른 처리"}
      </summary>
      <p className="mt-1 text-muted">
        만든 시각·만든 사람이 같고 제목·문장 같은 칸만 다른 행입니다. 기본은 둘 다 둡니다 (들어오는 행을 새 행으로).
        같은 자료를 수정한 것이면 "건너뛰기"를 고르세요 — 기존 행 그대로 두고, 그 행에 딸린 자료는 기존 행에 붙습니다.
        {total > pairs.length ? ` 앞 ${pairs.length}쌍만 보여 주고, 나머지는 둘 다 둡니다.` : ""}
      </p>
      <ul className={HAIRLINE_LIST}>
        {pairs.map((p) => {
          const key = nearKey(p);
          const decision = report.dry_run ? (choices[key] ?? "keep_both") : p.decision;
          return (
            <li key={key} className="space-y-2 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{short(p.table)}</span>
                <span className="text-xs text-muted">
                  다른 칸: {p.columns.map((c) => COLUMN_LABEL[c] ?? c).join(", ") || "—"}
                </span>
              </div>
              <dl className="grid grid-cols-1 gap-x-3 gap-y-0.5 text-xs md:grid-cols-[auto_1fr_1fr]">
                <span className="hidden md:block" />
                <span className="hidden text-muted md:block">이 공간 (기존)</span>
                <span className="hidden text-muted md:block">묶음 (들어옴)</span>
                {Object.keys(p.incoming).map((c) => (
                  <div key={c} className="contents">
                    <dt className="text-muted">{COLUMN_LABEL[c] ?? c}</dt>
                    <dd className="min-w-0 break-words">{shown(p.existing[c])}</dd>
                    <dd className={`min-w-0 break-words ${p.columns.includes(c) ? "font-semibold" : ""}`}>
                      {shown(p.incoming[c])}
                    </dd>
                  </div>
                ))}
              </dl>
              <div
                className="flex flex-wrap gap-x-4 text-[13px]"
                role="radiogroup"
                aria-label={`${short(p.table)} 처리`}
              >
                {(["keep_both", "skip_incoming"] as const).map((d) => (
                  <label key={d} className="flex min-h-9 items-center gap-1.5">
                    <input
                      type="radio"
                      name={`near-${key}`}
                      checked={decision === d}
                      disabled={!editable}
                      onChange={() => onChange(key, d)}
                    />
                    {d === "keep_both" ? "둘 다 두기" : "들어오는 행 건너뛰기"}
                  </label>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function UploadBar({ progress, name }: { progress: UploadProgress; name: string | undefined }) {
  const pct = progress.total > 0 ? Math.min(100, Math.round((progress.loaded / progress.total) * 100)) : 0;
  return (
    <div className="space-y-1.5 border-y border-line py-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">올리는 중</span>
        <span className="min-w-0 truncate text-muted">{name ?? "묶음"}</span>
      </div>
      <div>
        <div className="flex justify-between gap-2 text-xs text-muted">
          <span>{pct < 100 ? "서버로 보내는 중" : "서버가 받은 파일을 확인하는 중"}</span>
          <span className="tabular-nums">
            {bytes(progress.loaded)} / {bytes(progress.total)} · {pct}%
          </span>
        </div>
        <div
          className="mt-1 h-1.5 overflow-hidden rounded bg-subtle"
          role="progressbar"
          aria-label="업로드 진행"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
        >
          <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}

function JobStatus({ job }: { job: RestoreJob | RestoreJobMore }) {
  const pct = job.total > 0 ? Math.min(100, Math.round((job.done / job.total) * 100)) : null;
  const unit = job.phase === "read" || job.phase === "decrypt" ? bytes : int;
  return (
    <div className="space-y-1.5 border-y border-line py-3 text-[13px]">
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
          <div
            className="mt-1 h-1.5 overflow-hidden rounded bg-subtle"
            role="progressbar"
            aria-valuenow={pct ?? undefined}
          >
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
      <p className="font-mono text-xs break-all text-muted">sha256 {job.sha256}</p>
    </div>
  );
}

/** 실패만 배지로, 나머지 단계는 글자로 */
function StatusBadge({ job }: { job: RestoreJob | RestoreJobMore }) {
  switch (job.status) {
    case "uploaded":
      return <span className="font-medium">올림</span>;
    case "verifying":
      return <span className="font-medium">확인 중</span>;
    case "verified":
      return <span className="font-medium">확인됨 · 아직 쓰지 않음</span>;
    case "applying":
      return <span className="font-medium">합치는 중</span>;
    case "done":
      return <span className="font-medium">합침</span>;
    case "failed":
      return <Badge tone="danger">실패</Badge>;
  }
}

function ReportView({ report }: { report: MergeReportMore }) {
  const tables = report.tables.filter((t) => t.archive_rows > 0 && !(t.name in report.not_merged));
  return (
    <div className="space-y-4">
      <Fields>
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
        <Field label="확인">기존 행 그대로 · 더한 행 해시 일치</Field>
        {report.format_upgraded_from !== null ? (
          <Field label="형식">옛 형식 {report.format_upgraded_from} → 지금 형식으로 바꿔 읽음</Field>
        ) : null}
        {report.schema_converted_from ? (
          <Field label="버전">
            옛 설치({report.schema_converted_from.join(", ")})의 묶음 → 지금 표 구성으로 바꿔 읽음
          </Field>
        ) : null}
        {report.approvals_invalidated ? (
          <Field label="승인 요청">
            {int(report.approvals_invalidated)}건 {report.dry_run ? "닫힘 예정" : "닫음"} (대상 지원이 바뀜 · 다시 요청)
          </Field>
        ) : null}
      </Fields>
      {report.notes.map((n) => (
        <p key={n} className="text-[13px] font-medium">
          {n}
        </p>
      ))}
      {report.skipped_deletions.length ? (
        <details className="text-[13px]" open>
          <summary className="cursor-pointer py-1 text-muted hover:text-fg">
            적용하지 않은 삭제 기록 {report.skipped_deletions.length}건
          </summary>
          <ul className={HAIRLINE_LIST}>
            {report.skipped_deletions.map((d) => (
              <li key={`${d.target_table}:${d.target_id}`} className="py-2">
                <div className="flex flex-wrap justify-between gap-x-2">
                  <span>{short(d.target_table)}</span>
                  <span className="text-muted">{SKIP_REASON[d.reason] ?? d.reason}</span>
                </div>
                <div className="font-mono text-xs break-all text-muted">
                  {d.target_id}
                  {d.detail ? ` · ${d.detail}` : ""}
                </div>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details className="text-[13px]">
        <summary className="cursor-pointer py-1 text-muted hover:text-fg">표별 결과</summary>
        <ul className={HAIRLINE_LIST}>
          {tables.map((t) => (
            <li key={t.name} className="py-1.5">
              <div className="flex justify-between gap-2 tabular-nums">
                <span className="min-w-0 truncate">{short(t.name)}</span>
                <span className="shrink-0">
                  +{int(t.added)}
                  {t.skipped ? <span className="text-muted"> · 건너뜀 {int(t.skipped)}</span> : null}
                </span>
              </div>
              {Object.keys(t.skipped_by).length ? (
                <div className="truncate text-xs text-muted">
                  {Object.entries(t.skipped_by)
                    .map(([k, n]) => `${keyLabel(k)} ${n}`)
                    .join(" · ")}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[13px] text-muted">
          넣지 않는 표: {Object.keys(report.not_merged).map(short).join(", ")} (공간·구성원은 지금 그대로, 백업·삭제
          기록은 이 공간 것만)
        </p>
      </details>
    </div>
  );
}

/** 자동 정리(주기 유지보수) 상태: 마지막·다음 실행과 이 공간에서 한 일. */
function AutoMaintenanceLine({ auto }: { auto: NonNullable<Maintenance["auto"]> }) {
  if (auto.interval_seconds === 0) {
    return <p className="text-[13px] text-muted">자동 정리: 꺼짐 (CAREER_MAINTENANCE_INTERVAL_SECONDS=0)</p>;
  }
  const done = Object.entries(auto.counts).filter(([, n]) => n > 0);
  return (
    <p className="text-[13px] text-muted">
      자동 정리:{" "}
      {auto.running
        ? "실행 중"
        : auto.last_finished_at
          ? `마지막 ${formatDateTime(auto.last_finished_at)}`
          : "아직 안 돎"}
      {auto.next_at ? ` · 다음 ${formatDateTime(auto.next_at)}` : ""}
      {done.length ? ` · 이 공간: ${done.map(([k, n]) => `${k} ${int(n)}`).join(", ")}` : ""}
      {auto.errors ? ` · 오류 ${int(auto.errors)}` : ""}
    </p>
  );
}
