import type { ExperienceSummary, SourceListItem } from "@career-brain/client";
import { Button, cn, Input, Label, Sheet, type BadgeTone } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus, Upload } from "lucide-react";
import { useState, type ReactNode } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { InterviewBatchButton } from "../components/runs/InterviewBatchSheet";
import { UploadSheet } from "../components/UploadSheet";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { EXTRACTION_STATUS, ORIGIN_LABEL, PARSE_STATUS } from "../lib/labels";

const route = getRouteApi("/brain");

function confirmedCount(e: ExperienceSummary) {
  return (e.claim_counts.self_confirmed ?? 0) + (e.claim_counts.evidence_supported ?? 0);
}

export function BrainPage() {
  const { review } = route.useSearch();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["experiences"], queryFn: api.experiences });
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const pendingTotal = (q.data ?? []).reduce((n, e) => n + e.needs_review, 0);
  const summary = [
    q.data ? `경험 ${q.data.length}` : null,
    pendingTotal ? `확인할 주장 ${pendingTotal}` : null,
    "확인한 주장만 문서와 검색 근거로 씁니다.",
  ].filter(Boolean);

  return (
    <div>
      <PageHeader
        title="브레인"
        subtitle={summary.join(" · ")}
        actions={
          <>
            <InterviewBatchButton experiences={q.data ?? []} />
            <Button onClick={() => setUploading(true)}>
              <Upload size={15} /> 자료 올리기
            </Button>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus size={15} /> 경험 추가
            </Button>
          </>
        }
      />

      <div className="mb-3 flex items-center gap-1.5" role="radiogroup" aria-label="보기">
        {[
          { v: false, label: "전체" },
          { v: true, label: `확인 필요${pendingTotal ? ` ${pendingTotal}` : ""}` },
        ].map((f) => (
          <button
            key={String(f.v)}
            role="radio"
            aria-checked={!!review === f.v}
            onClick={() => navigate({ to: "/brain", search: f.v ? { review: true } : {} })}
            className={cn(
              "h-9 rounded-full px-3.5 text-[13px] tabular-nums transition-colors duration-150",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              !!review === f.v ? "bg-fg font-medium text-bg" : "text-muted hover:bg-subtle hover:text-fg",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      <Loaded query={q}>
        {(items) => {
          const shown = review ? items.filter((e) => e.needs_review > 0) : items;
          if (!shown.length)
            return review ? (
              <Quiet>
                확인할 주장이 없습니다.{" "}
                <Link to="/brain" className="text-fg underline underline-offset-2">
                  전체 보기
                </Link>
              </Quiet>
            ) : (
              <Quiet>
                아직 경험이 없습니다. 이력서·포트폴리오를 올리거나 직접 추가하세요.{" "}
                <button
                  type="button"
                  onClick={() => setUploading(true)}
                  className="text-fg underline underline-offset-2"
                >
                  자료 올리기
                </button>
              </Quiet>
            );
          return (
            <ul className="divide-y divide-line border-t border-line">
              {shown.map((e) => (
                <li key={e.id}>
                  <Link
                    to="/brain/$experienceId"
                    params={{ experienceId: e.id }}
                    className="-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{e.title}</div>
                      <div className="truncate text-[13px] text-muted">
                        {[e.organization, e.role, e.period_text, ORIGIN_LABEL[e.origin] ?? e.origin]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </div>
                    <ClaimState e={e} />
                  </Link>
                </li>
              ))}
            </ul>
          );
        }}
      </Loaded>

      <SourcesList />

      <CreateExperienceSheet open={creating} onClose={() => setCreating(false)} />
      <UploadSheet open={uploading} onClose={() => setUploading(false)} />
    </div>
  );
}

/** 오른쪽 상태: 확인할 것이 있으면 진하게, 나머지는 회색 한 줄. */
function ClaimState({ e }: { e: ExperienceSummary }) {
  const confirmed = confirmedCount(e);
  return (
    <div className="shrink-0 text-right text-[13px] leading-tight tabular-nums">
      {e.needs_review ? <div className="font-semibold">확인 필요 {e.needs_review}</div> : null}
      {confirmed ? <div className="text-muted">확인됨 {confirmed}</div> : null}
      {!e.needs_review && !confirmed ? <div className="text-muted">주장 없음</div> : null}
    </div>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

function CreateExperienceSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ title: "", organization: "", role: "", period_text: "" });
  const create = useMutation({
    mutationFn: () =>
      api.createExperience({
        title: form.title.trim(),
        organization: form.organization.trim() || null,
        role: form.role.trim() || null,
        period_text: form.period_text.trim() || null,
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["experiences"] });
      setForm({ title: "", organization: "", role: "", period_text: "" });
      onClose();
      void navigate({ to: "/brain/$experienceId", params: { experienceId: res.id } });
    },
  });
  const field = (key: keyof typeof form, label: string, placeholder: string) => (
    <div>
      <Label htmlFor={`exp-${key}`}>{label}</Label>
      <Input
        id={`exp-${key}`}
        value={form[key]}
        placeholder={placeholder}
        maxLength={key === "period_text" ? 100 : 300}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
      />
    </div>
  );
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="경험 추가">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (form.title.trim()) create.mutate();
        }}
      >
        {field("title", "제목", "예: 주문 처리 시스템 개편")}
        {field("organization", "조직", "예: 회사·학교·동아리")}
        {field("role", "역할", "예: 백엔드 개발")}
        {field("period_text", "기간", "예: 2024.03 - 2024.12")}
        <ErrorText error={create.error} />
        <Button type="submit" variant="primary" disabled={!form.title.trim() || create.isPending}>
          추가
        </Button>
      </form>
    </Sheet>
  );
}

function size(n: number | null) {
  if (n === null) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 올린 원본과 읽기·추출 상태. 화면의 중심이 아니라 아래에 접어 둔다 (plan §13). */
function SourcesList() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.sources(),
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.parse_status === "pending" || s.extraction_status === "pending") ? 4000 : false,
  });
  const extract = useMutation({
    mutationFn: (runId: string) => api.requestExtraction(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
  const rows = q.data ?? [];
  if (!rows.length && !q.isError) return null;
  return (
    <details className="group mt-10">
      <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1 text-[15px] font-semibold [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} className="text-muted transition-transform duration-150 group-open:rotate-90" />
        올린 자료 <span className="font-normal text-muted tabular-nums">{rows.length}</span>
      </summary>
      <ErrorText error={q.error ?? extract.error} className="mt-2" />
      <ul className="mt-2 divide-y divide-line border-t border-line">
        {rows.map((src: SourceListItem) => {
          const parse = src.parse_status ? PARSE_STATUS[src.parse_status] : null;
          const ext = src.extraction_status ? EXTRACTION_STATUS[src.extraction_status] : null;
          const canExtract =
            src.parse_run_id &&
            (src.parse_status === "complete" || src.parse_status === "partial") &&
            !src.extraction_status;
          return (
            <li key={src.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
              <div className="min-w-0 flex-1 basis-56">
                <div className="truncate text-sm font-medium">{src.title}</div>
                <div className="truncate text-[13px] text-muted tabular-nums">
                  {[
                    src.original_filename,
                    size(src.size_bytes),
                    src.pages_total ? `${src.pages_total}쪽` : null,
                    formatDate(src.created_at),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
                {src.parse_error ? <div className="text-[13px] break-words text-danger">{src.parse_error}</div> : null}
              </div>
              <div className="flex shrink-0 items-center gap-3 text-[13px] tabular-nums">
                {parse ? <span className={TONE_TEXT[parse.tone]}>{parse.label}</span> : null}
                {ext ? (
                  <span className={TONE_TEXT[ext.tone]}>
                    {ext.label}
                    {src.claims_created ? ` ${src.claims_created}` : ""}
                  </span>
                ) : null}
                {canExtract ? (
                  <Button
                    size="sm"
                    className="max-sm:h-9"
                    onClick={() => extract.mutate(src.parse_run_id!)}
                    disabled={extract.isPending}
                  >
                    경험 추출 요청
                  </Button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[13px] text-muted">
        경험 추출은 원문을 모델로 보내며 비용이 듭니다. 추출한 주장은 확인 전까지 근거로 쓰지 않습니다.
      </p>
    </details>
  );
}

/** 배지 대신 글자로: 실패만 빨강, 확인이 필요한 상태는 진하게, 나머지는 회색. */
const TONE_TEXT: Record<BadgeTone, string> = {
  ok: "text-muted",
  none: "text-muted",
  neutral: "text-muted",
  accent: "text-muted",
  warn: "font-semibold",
  danger: "font-medium text-danger",
};
