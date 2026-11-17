import type { ExperienceIn, ExperienceKind, ExperienceStructure } from "@career-brain/client";
import { Button, Input, Label, Select } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { api } from "../lib/api";
import { EXPERIENCE_KIND_LABEL, EXPERIENCE_KINDS, PARENT_KINDS, splitTags } from "../lib/experience";
import { ErrorText } from "./common";

export interface ExperienceFormValues {
  title: string;
  kind: ExperienceKind;
  parent_id: string;
  organization: string;
  role: string;
  started_on: string;
  ended_on: string;
  ongoing: boolean;
  period_text: string;
  tags: string;
}

export const EMPTY_EXPERIENCE: ExperienceFormValues = {
  title: "",
  kind: "other",
  parent_id: "",
  organization: "",
  role: "",
  started_on: "",
  ended_on: "",
  ongoing: false,
  period_text: "",
  tags: "",
};

export function formValues(
  e: ExperienceStructure & {
    title: string;
    organization: string | null;
    role: string | null;
    period_text: string | null;
  },
): ExperienceFormValues {
  return {
    title: e.title,
    kind: e.kind,
    parent_id: e.parent_id ?? "",
    organization: e.organization ?? "",
    role: e.role ?? "",
    started_on: e.started_on ?? "",
    ended_on: e.ended_on ?? "",
    ongoing: e.ongoing,
    period_text: e.period_text ?? "",
    tags: e.tags.join(", "),
  };
}

export function toBody(v: ExperienceFormValues): ExperienceIn {
  return {
    title: v.title.trim(),
    kind: v.kind,
    parent_id: v.parent_id || null,
    organization: v.organization.trim() || null,
    role: v.role.trim() || null,
    started_on: v.started_on || null,
    ended_on: v.ongoing ? null : v.ended_on || null,
    ongoing: v.ongoing,
    period_text: v.period_text.trim() || null,
    tags: splitTags(v.tags),
  };
}

/**
 * 경험 만들기·고치기 폼. 기간은 달(type=month)로, 원문 기간은 따로 둔다 (모호하면 날짜를 비워 둔다).
 * 상위 경험은 한 단계만: 맨 위의 경력·교육 경험 중에서 고른다.
 */
export function ExperienceForm({
  initial,
  selfId,
  hasChildren = false,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial: ExperienceFormValues;
  /** 고치는 경험 (상위 후보에서 뺀다) */
  selfId?: string;
  hasChildren?: boolean;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (values: ExperienceFormValues) => void;
  onCancel?: () => void;
}) {
  const [v, setV] = useState(initial);
  const parents = useQuery({
    queryKey: ["experiences", "parents"],
    queryFn: () => api.experiences({ topLevel: true, kind: PARENT_KINDS, sort: "title" }),
  });
  const set = <K extends keyof ExperienceFormValues>(key: K, value: ExperienceFormValues[K]) =>
    setV((prev) => ({ ...prev, [key]: value }));
  const options = (parents.data ?? []).filter((p) => p.id !== selfId);
  const text = (key: "title" | "organization" | "role" | "period_text", label: string, placeholder: string) => (
    <div>
      <Label htmlFor={`exp-${key}`}>{label}</Label>
      <Input
        id={`exp-${key}`}
        value={v[key]}
        placeholder={placeholder}
        maxLength={key === "period_text" ? 100 : 300}
        onChange={(e) => set(key, e.target.value)}
      />
    </div>
  );

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (v.title.trim()) onSubmit(v);
      }}
    >
      {text("title", "제목", "예: 주문 처리 시스템 개편")}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="exp-kind">종류</Label>
          <Select id="exp-kind" value={v.kind} onChange={(e) => set("kind", e.target.value as ExperienceKind)}>
            {EXPERIENCE_KINDS.map((k) => (
              <option key={k} value={k}>
                {EXPERIENCE_KIND_LABEL[k]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="exp-parent">상위 경험</Label>
          <Select
            id="exp-parent"
            value={v.parent_id}
            disabled={hasChildren}
            onChange={(e) => set("parent_id", e.target.value)}
          >
            <option value="">없음 (맨 위)</option>
            {options.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
                {p.organization ? ` · ${p.organization}` : ""}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {hasChildren ? (
        <p className="text-[13px] text-muted">안에 경험이 있어 다른 경험 아래로 옮길 수 없습니다 (한 단계만).</p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {text("organization", "조직", "예: 회사·학교·동아리")}
        {text("role", "역할", "예: 백엔드 개발")}
      </div>
      <fieldset className="space-y-2">
        <legend className="text-[13px] font-medium text-muted">기간 (달)</legend>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="exp-start" className="sr-only">
              시작 달
            </Label>
            <Input
              id="exp-start"
              type="month"
              value={v.started_on}
              onChange={(e) => set("started_on", e.target.value)}
              aria-label="시작 달"
              className="tabular-nums"
            />
          </div>
          <div>
            <Label htmlFor="exp-end" className="sr-only">
              끝 달
            </Label>
            <Input
              id="exp-end"
              type="month"
              value={v.ongoing ? "" : v.ended_on}
              disabled={v.ongoing}
              min={v.started_on || undefined}
              onChange={(e) => set("ended_on", e.target.value)}
              aria-label="끝 달"
              className="tabular-nums"
            />
          </div>
        </div>
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4 shrink-0 accent-fg"
            checked={v.ongoing}
            onChange={(e) => set("ongoing", e.target.checked)}
          />
          진행 중
        </label>
      </fieldset>
      {text("period_text", "원문 기간", "예: 2024.03 - 2024.12 (자료에 적힌 그대로)")}
      <div>
        <Label htmlFor="exp-tags">태그</Label>
        <Input
          id="exp-tags"
          value={v.tags}
          placeholder="예: PostgreSQL, Kafka, 결제"
          onChange={(e) => set("tags", e.target.value)}
        />
        <p className="mt-1 text-[13px] text-muted">쉼표로 나눕니다. 기술·주제 낱말, 30개까지.</p>
      </div>
      <ErrorText error={error} />
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!v.title.trim() || pending}>
          {submitLabel}
        </Button>
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel}>
            취소
          </Button>
        ) : null}
      </div>
    </form>
  );
}

/** 경험 만들기 (브레인·경험 화면에서 같이 쓴다). 만든 뒤 그 경험으로 간다. */
export function CreateExperienceForm({
  initial,
  onDone,
}: {
  initial: ExperienceFormValues;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: (v: ExperienceFormValues) => api.createExperience(toBody(v)),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["experiences"] });
      qc.invalidateQueries({ queryKey: ["experience"] });
      onDone();
      void navigate({ to: "/brain/$experienceId", params: { experienceId: res.id } });
    },
  });
  return (
    <ExperienceForm
      initial={initial}
      submitLabel="추가"
      pending={create.isPending}
      error={create.error}
      onSubmit={(v) => create.mutate(v)}
    />
  );
}

