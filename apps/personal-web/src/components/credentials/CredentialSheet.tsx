import type { Credential, CredentialField, CredentialIn, CredentialKind } from "@career-brain/client";
import { Button, ConfirmDialog, DateField, Input, Label, Select, SelectItem, Sheet, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { CREDENTIAL_KIND_OPTIONS, CREDENTIAL_STATUS_LABEL } from "../../lib/credentials";
import { ErrorText } from "../common";

/** 추가 (credential 없음) 또는 수정. 후보(extracted)면 "저장하고 확인"까지 한 번에. */
export function CredentialSheet({
  open,
  credential,
  defaultSourceId,
  onClose,
}: {
  open: boolean;
  credential: Credential | null;
  /** 추가할 때 미리 이을 증빙 (자료 화면에서 열 때) */
  defaultSourceId?: string;
  onClose: () => void;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={credential ? (credential.status === "extracted" ? "뽑은 항목 확인" : "기록 수정") : "기록 추가"}
      description={
        credential?.status === "extracted"
          ? "증빙 서류에서 뽑은 값입니다. 원문과 맞는지 보고 확인합니다."
          : credential
            ? CREDENTIAL_STATUS_LABEL[credential.status]
            : undefined
      }
    >
      {open ? (
        <CredentialForm
          key={credential?.id ?? "new"}
          credential={credential}
          defaultSourceId={defaultSourceId}
          onDone={onClose}
        />
      ) : null}
    </Sheet>
  );
}

interface Draft {
  kind: CredentialKind;
  name: string;
  issuer: string;
  acquired_on: string;
  expires_on: string;
  started_on: string;
  ended_on: string;
  score: string;
  hours: string;
  detail: string;
  source_id: string;
  experience_id: string;
}

function draftOf(c: Credential | null, sourceId?: string): Draft {
  return {
    kind: c?.kind ?? "license",
    name: c?.name ?? "",
    issuer: c?.issuer ?? "",
    acquired_on: c?.acquired_on ?? "",
    expires_on: c?.expires_on ?? "",
    started_on: c?.started_on ?? "",
    ended_on: c?.ended_on ?? "",
    score: c?.score ?? "",
    hours: c?.hours ? String(c.hours) : "",
    detail: c?.detail ?? "",
    source_id: c?.source_id ?? sourceId ?? "",
    experience_id: c?.experience_id ?? "",
  };
}

const orNull = (v: string) => (v.trim() ? v.trim() : null);

function CredentialForm({
  credential: c,
  defaultSourceId,
  onDone,
}: {
  credential: Credential | null;
  defaultSourceId?: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(() => draftOf(c, defaultSourceId));
  // 번호: null = 손대지 않음 (가린 값만 보인다). 보기를 누르거나 새로 적으면 문자열
  const [number, setNumber] = useState<string | null>(c ? null : "");
  const [removing, setRemoving] = useState(false);
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));

  const proofs = useQuery({ queryKey: ["sources", "proofs"], queryFn: () => api.proofs() });
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });
  const reveal = useMutation({
    mutationFn: () => api.credential(c!.id, true),
    onSuccess: (full) => setNumber(full.number ?? ""),
  });

  const body = (): CredentialIn => {
    const out: CredentialIn = {
      kind: d.kind,
      name: d.name.trim(),
      issuer: orNull(d.issuer),
      acquired_on: orNull(d.acquired_on),
      expires_on: orNull(d.expires_on),
      started_on: orNull(d.started_on),
      ended_on: orNull(d.ended_on),
      score: orNull(d.score),
      hours: d.hours.trim() ? Number(d.hours) : null,
      detail: orNull(d.detail),
      source_id: orNull(d.source_id),
      experience_id: orNull(d.experience_id),
    };
    if (number !== null) out.number = orNull(number);
    return out;
  };
  const done = () => {
    void qc.invalidateQueries({ queryKey: ["credentials"] });
    void qc.invalidateQueries({ queryKey: ["sources", "proofs"] });
    onDone();
  };
  const save = useMutation({
    mutationFn: async (confirm: boolean) => {
      if (!c) return api.createCredential(body());
      // 바뀐 칸만 보낸다 (그대로인 칸은 원문 위치를 지키게)
      const next = body();
      const patch: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(next)) {
        if (k === "number" || (c as unknown as Record<string, unknown>)[k] !== v) patch[k] = v;
      }
      if (Object.keys(patch).length) await api.updateCredential(c.id, patch);
      if (confirm) await api.confirmCredential(c.id);
      return { id: c.id };
    },
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => api.deleteCredential(c!.id), onSuccess: done });
  const toEducation = useMutation({
    mutationFn: () => api.moveCredentialToEducation(c!.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["education"] });
      done();
    },
  });

  const span = (f: CredentialField) => c?.field_spans[f];
  const hint = (f: CredentialField) =>
    span(f) ? (
      <p className="mt-1 text-xs text-muted tabular-nums">
        원문에서 뽑음 · 블록 {span(f)!.block + 1}
        {span(f)!.page ? ` · ${span(f)!.page}쪽` : ""}
      </p>
    ) : null;
  const periodKinds: CredentialKind[] = ["course", "degree", "other"];
  const valid = d.name.trim() !== "" && (!d.hours.trim() || /^\d{1,6}$/.test(d.hours.trim()));
  const candidate = c?.status === "extracted";

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) save.mutate(candidate);
      }}
    >
      <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3">
        <Field label="종류" htmlFor="cr-kind">
          <Select id="cr-kind" value={d.kind} onValueChange={(v) => set({ kind: v as CredentialKind })}>
            {/* 학력은 학력 화면에서 적는다 (옛 학력 기록만 이 종류로 보인다) */}
            {CREDENTIAL_KIND_OPTIONS.filter((o) => o.value !== "degree" || c?.kind === "degree").map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </Select>
        </Field>
        <Field label="이름" htmlFor="cr-name" hint={hint("name")}>
          <Input
            id="cr-name"
            value={d.name}
            onChange={(e) => set({ name: e.target.value })}
            maxLength={200}
            required
            placeholder={d.kind === "language" ? "TOEIC" : d.kind === "course" ? "과정 이름" : "정보처리기사"}
          />
        </Field>
      </div>
      <Field label="발급 기관" htmlFor="cr-issuer" hint={hint("issuer")}>
        <Input id="cr-issuer" value={d.issuer} onChange={(e) => set({ issuer: e.target.value })} maxLength={200} />
      </Field>
      <Field label="번호" htmlFor="cr-number" hint={hint("number")}>
        {number === null ? (
          <div className="flex min-h-9 items-center gap-3">
            <span className="text-sm tabular-nums">{c?.number ?? "없음"}</span>
            {c?.number ? (
              <button
                type="button"
                className="inline-flex min-h-9 items-center text-[13px] text-muted underline underline-offset-2 hover:text-fg"
                onClick={() => reveal.mutate()}
                disabled={reveal.isPending}
              >
                번호 보기
              </button>
            ) : null}
            <button
              type="button"
              className="inline-flex min-h-9 items-center text-[13px] text-muted underline underline-offset-2 hover:text-fg"
              onClick={() => setNumber("")}
            >
              {c?.number ? "새로 입력" : "입력"}
            </button>
          </div>
        ) : (
          <Input
            id="cr-number"
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            maxLength={100}
            autoComplete="off"
            className="tabular-nums"
          />
        )}
        <ErrorText error={reveal.error} className="mt-1" />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label={d.kind === "language" ? "응시일" : "취득일"} htmlFor="cr-acquired" hint={hint("acquired_on")}>
          <DateField id="cr-acquired" value={d.acquired_on} onChange={(value) => set({ acquired_on: value })} />
        </Field>
        <Field label="만료일" htmlFor="cr-expires" hint={hint("expires_on")}>
          <DateField id="cr-expires" value={d.expires_on} onChange={(value) => set({ expires_on: value })} />
        </Field>
      </div>
      {d.kind === "language" && !d.expires_on ? (
        <p className="-mt-2 text-xs text-muted">
          어학 성적은 보통 2년 유효합니다. 성적표에 적힌 날을 넣으면 오늘 화면 일정에 나옵니다.
        </p>
      ) : null}
      {periodKinds.includes(d.kind) || d.started_on || d.ended_on ? (
        <div className="grid grid-cols-2 gap-3">
          <Field label="시작일" htmlFor="cr-start" hint={hint("started_on")}>
            <DateField id="cr-start" value={d.started_on} onChange={(value) => set({ started_on: value })} />
          </Field>
          <Field label="종료일" htmlFor="cr-end" hint={hint("ended_on")}>
            <DateField id="cr-end" value={d.ended_on} onChange={(value) => set({ ended_on: value })} />
          </Field>
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3">
        <Field label="점수·등급" htmlFor="cr-score" hint={hint("score")}>
          <Input id="cr-score" value={d.score} onChange={(e) => set({ score: e.target.value })} maxLength={100} />
        </Field>
        {d.kind === "course" || d.hours ? (
          <Field label="시간" htmlFor="cr-hours" hint={hint("hours")}>
            <Input
              id="cr-hours"
              inputMode="numeric"
              value={d.hours}
              onChange={(e) => set({ hours: e.target.value })}
              className="tabular-nums"
            />
          </Field>
        ) : null}
      </div>
      <Field label="증빙 서류" htmlFor="cr-source">
        <Select id="cr-source" value={d.source_id} onValueChange={(v) => set({ source_id: v })}>
          <SelectItem value="">없음</SelectItem>
          {(proofs.data ?? []).map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.title}
            </SelectItem>
          ))}
        </Select>
      </Field>
      <Field label="이어진 경험" htmlFor="cr-exp">
        <Select id="cr-exp" value={d.experience_id} onValueChange={(v) => set({ experience_id: v })}>
          <SelectItem value="">없음</SelectItem>
          {(experiences.data ?? []).map((x) => (
            <SelectItem key={x.id} value={x.id}>
              {x.title}
            </SelectItem>
          ))}
        </Select>
      </Field>
      <Field label="상세" htmlFor="cr-detail">
        <Textarea id="cr-detail" value={d.detail} onChange={(e) => set({ detail: e.target.value })} maxLength={2000} />
      </Field>

      {c?.kind === "degree" ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3 text-[13px] text-muted">
          <span>학력은 이제 학력 화면에 둡니다.</span>
          <Button size="sm" className="max-sm:h-9" onClick={() => toEducation.mutate()} disabled={toEducation.isPending}>
            학력으로 옮기기
          </Button>
          <ErrorText error={toEducation.error} className="basis-full" />
        </div>
      ) : null}
      <ErrorText error={save.error} />
      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <Button type="submit" variant="primary" disabled={!valid || save.isPending}>
          {candidate ? "저장하고 확인" : c ? "저장" : "추가"}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          취소
        </Button>
        {c ? (
          <Button variant="ghost" className="ml-auto text-danger" onClick={() => setRemoving(true)}>
            삭제
          </Button>
        ) : null}
      </div>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={`"${c?.name ?? ""}" 삭제`}
        description="이 기록을 삭제합니다. 증빙 서류 원본은 그대로 남습니다."
        confirmLabel="삭제"
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      >
        <ErrorText error={remove.error} />
      </ConfirmDialog>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <Label htmlFor={htmlFor} className="mb-1">
        {label}
      </Label>
      {children}
      {hint}
    </div>
  );
}
