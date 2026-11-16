import type { ExperienceSummary } from "@career-brain/client";
import { Badge, Button, EmptyState, Input, Label, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus, Upload } from "lucide-react";
import { useState } from "react";
import { ErrorText, List, Loaded, PageHeader } from "../components/common";
import { UploadSheet } from "../components/UploadSheet";
import { api } from "../lib/api";
import { ORIGIN_LABEL } from "../lib/labels";

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
