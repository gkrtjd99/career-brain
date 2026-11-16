import type { ExperienceSummary, SourceListItem } from "@career-brain/client";
import { Badge, Button, EmptyState, Input, Label, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus, Upload } from "lucide-react";
import { useState } from "react";
import { ErrorText, List, Loaded, PageHeader } from "../components/common";
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

  return (
    <div>
      <PageHeader
        title="브레인"
        subtitle="확인·보강할 경험 목록. 확인한 주장만 문서와 검색 근거로 쓰입니다."
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

      <div className="mb-3 flex items-center gap-2" role="radiogroup" aria-label="보기">
        {[
          { v: false, label: "전체" },
          { v: true, label: `확인 필요${pendingTotal ? ` ${pendingTotal}` : ""}` },
        ].map((f) => (
          <button
            key={String(f.v)}
            role="radio"
            aria-checked={!!review === f.v}
            onClick={() => navigate({ to: "/brain", search: f.v ? { review: true } : {} })}
            className={
              !!review === f.v
                ? "rounded-full bg-fg px-3 py-1 text-[13px] font-medium text-bg"
                : "rounded-full border border-line bg-surface px-3 py-1 text-[13px] text-muted hover:text-fg"
            }
          >
            {f.label}
          </button>
        ))}
      </div>

      <Loaded query={q}>
        {(items) => {
          const shown = review ? items.filter((e) => e.needs_review > 0) : items;
          if (!shown.length)
            return (
              <EmptyState>
                {review
                  ? "확인할 주장이 없습니다."
                  : "아직 경험이 없습니다. 이력서·포트폴리오를 올리거나 직접 추가하세요."}
              </EmptyState>
            );
          return (
            <List>
              {shown.map((e) => (
                <li key={e.id}>
                  <Link
                    to="/brain/$experienceId"
                    params={{ experienceId: e.id }}
                    className="flex items-center gap-3 px-3 py-3 hover:bg-subtle/60"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{e.title}</div>
                      <div className="truncate text-xs text-muted">
                        {[e.organization, e.role, e.period_text, ORIGIN_LABEL[e.origin] ?? e.origin]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap justify-end gap-1">
                      {e.needs_review ? <Badge tone="warn">확인 필요 {e.needs_review}</Badge> : null}
                      {confirmedCount(e) ? <Badge tone="ok">확인됨 {confirmedCount(e)}</Badge> : null}
                      {!e.needs_review && !confirmedCount(e) ? <Badge tone="none">주장 없음</Badge> : null}
                    </div>
                    <ChevronRight size={16} className="shrink-0 text-muted" />
                  </Link>
                </li>
              ))}
            </List>
          );
        }}
      </Loaded>

      <SourcesList />

      <CreateExperienceSheet open={creating} onClose={() => setCreating(false)} />
      <UploadSheet open={uploading} onClose={() => setUploading(false)} />
    </div>
  );
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
    <details className="mt-8">
      <summary className="cursor-pointer text-[13px] font-medium text-muted">올린 자료 {rows.length}개</summary>
      <ErrorText error={q.error ?? extract.error} className="mt-2" />
      <List className="mt-2">
        {rows.map((src: SourceListItem) => {
          const parse = src.parse_status ? PARSE_STATUS[src.parse_status] : null;
          const ext = src.extraction_status ? EXTRACTION_STATUS[src.extraction_status] : null;
          const canExtract =
            src.parse_run_id && (src.parse_status === "complete" || src.parse_status === "partial") && !src.extraction_status;
          return (
            <li key={src.id} className="flex flex-wrap items-center gap-2 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{src.title}</div>
                <div className="truncate text-xs text-muted">
                  {[src.original_filename, size(src.size_bytes), src.pages_total ? `${src.pages_total}쪽` : null, formatDate(src.created_at)]
                    .filter(Boolean)
                    .join(" · ")}
                  {src.parse_error ? ` · ${src.parse_error}` : ""}
                </div>
              </div>
              {parse ? <Badge tone={parse.tone}>{parse.label}</Badge> : null}
              {ext ? (
                <Badge tone={ext.tone}>
                  {ext.label}
                  {src.claims_created ? ` ${src.claims_created}` : ""}
                </Badge>
              ) : null}
              {canExtract ? (
                <Button size="sm" onClick={() => extract.mutate(src.parse_run_id!)} disabled={extract.isPending}>
                  경험 추출 요청
                </Button>
              ) : null}
            </li>
          );
        })}
      </List>
      <p className="mt-1 text-xs text-muted">경험 추출은 원문을 모델로 보내며 비용이 듭니다. 추출한 주장은 확인 전까지 근거로 쓰지 않습니다.</p>
    </details>
  );
}
