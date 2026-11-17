import {
  DEGREES,
  EDUCATION_STATUSES,
  GPA_SCALES,
  type Degree,
  type Education,
  type EducationIn,
  type EducationStatus,
  type ExtraMajorKind,
} from "@career-brain/client";
import { Button, ConfirmDialog, Input, Label, Select, Sheet, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { DEGREE_LABEL, EDUCATION_STATUS_LABEL, EXTRA_MAJOR_LABEL, meErrorText } from "../../lib/brainMe";
import { errorText } from "../common";

/** 학력 추가 (education 없음) 또는 고치기 */
export function EducationSheet({
  open,
  education,
  onClose,
}: {
  open: boolean;
  education: Education | null;
  onClose: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title={education ? "학력 수정" : "학력 추가"}>
      {open ? <EducationForm key={education?.id ?? "new"} education={education} onDone={onClose} /> : null}
    </Sheet>
  );
}

interface Draft {
  school: string;
  campus: string;
  major: string;
  extra_major: string;
  extra_major_kind: ExtraMajorKind | "";
  degree: Degree;
  status: EducationStatus;
  transferred: boolean;
  entered_on: string;
  graduated_on: string;
  gpa: string;
  gpa_scale: string;
  note: string;
  source_id: string;
  experience_id: string;
}

const month = (d: string | null) => (d ? d.slice(0, 7) : "");

function draftOf(e: Education | null): Draft {
  return {
    school: e?.school ?? "",
    campus: e?.campus ?? "",
    major: e?.major ?? "",
    extra_major: e?.extra_major ?? "",
    extra_major_kind: e?.extra_major_kind ?? "",
    degree: e?.degree ?? "bachelor",
    status: e?.status ?? "graduated",
    transferred: e?.transferred ?? false,
    entered_on: month(e?.entered_on ?? null),
    graduated_on: month(e?.graduated_on ?? null),
    gpa: e?.gpa !== null && e?.gpa !== undefined ? String(e.gpa) : "",
    gpa_scale: e?.gpa_scale !== null && e?.gpa_scale !== undefined ? String(e.gpa_scale) : "4.5",
    note: e?.note ?? "",
    source_id: e?.source_id ?? "",
    experience_id: e?.experience_id ?? "",
  };
}

const orNull = (v: string) => (v.trim() ? v.trim() : null);

function bodyOf(d: Draft): EducationIn & { school: string } {
  const gpa = d.gpa.trim() ? Number(d.gpa) : null;
  return {
    school: d.school.trim(),
    campus: orNull(d.campus),
    major: d.degree === "high_school" ? null : orNull(d.major),
    extra_major: d.degree === "high_school" ? null : orNull(d.extra_major),
    extra_major_kind: d.degree !== "high_school" && d.extra_major.trim() ? d.extra_major_kind || null : null,
    degree: d.degree,
    status: d.status,
    transferred: d.transferred,
    entered_on: orNull(d.entered_on),
    graduated_on: orNull(d.graduated_on),
    gpa,
    gpa_scale: gpa === null ? null : Number(d.gpa_scale),
    note: orNull(d.note),
    source_id: orNull(d.source_id),
    experience_id: orNull(d.experience_id),
  };
}

/** 화면에서 먼저 보는 규칙 (서버도 같은 규칙을 본다) */
function problem(d: Draft): string | null {
  if (!d.school.trim()) return "학교 이름을 적어 주세요.";
  if (d.gpa.trim()) {
    const g = Number(d.gpa);
    if (!Number.isFinite(g) || g < 0) return "학점은 0 이상의 숫자로 적어 주세요.";
    if (g > Number(d.gpa_scale)) return "학점이 만점보다 큽니다.";
  }
  if (d.entered_on && d.graduated_on && d.graduated_on < d.entered_on) return "졸업 달이 입학 달보다 빠릅니다.";
  if (d.extra_major.trim() && !d.extra_major_kind && d.degree !== "high_school")
    return "복수전공인지 부전공인지 골라 주세요.";
  return null;
}

function EducationForm({ education: e, onDone }: { education: Education | null; onDone: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(() => draftOf(e));
  const [removing, setRemoving] = useState(false);
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const proofs = useQuery({ queryKey: ["sources", "proofs"], queryFn: () => api.proofs() });
  const experiences = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });

  const done = () => {
    void qc.invalidateQueries({ queryKey: ["education"] });
    onDone();
  };
  const save = useMutation({
    mutationFn: async () => {
      const body = bodyOf(d);
      if (e) await api.patchEducation(e.id, body);
      else await api.createEducation(body);
    },
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => api.deleteEducation(e!.id), onSuccess: done });
  const issue = problem(d);
  const highSchool = d.degree === "high_school";

  return (
    <form
      className="space-y-4"
      onSubmit={(ev) => {
        ev.preventDefault();
        if (!issue) save.mutate();
      }}
    >
      <div className="grid grid-cols-[minmax(0,1fr)_8rem] gap-3">
        <Field label="학교" htmlFor="ed-school">
          <Input
            id="ed-school"
            value={d.school}
            onChange={(ev) => set({ school: ev.target.value })}
            maxLength={100}
            required
            placeholder="가상대학교"
          />
        </Field>
        <Field label="캠퍼스" htmlFor="ed-campus">
          <Input id="ed-campus" value={d.campus} onChange={(ev) => set({ campus: ev.target.value })} maxLength={50} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="학위 과정" htmlFor="ed-degree">
          <Select id="ed-degree" value={d.degree} onChange={(ev) => set({ degree: ev.target.value as Degree })}>
            {DEGREES.map((v) => (
              <option key={v} value={v}>
                {DEGREE_LABEL[v]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="상태" htmlFor="ed-status">
          <Select
            id="ed-status"
            value={d.status}
            onChange={(ev) => set({ status: ev.target.value as EducationStatus })}
          >
            {EDUCATION_STATUSES.map((v) => (
              <option key={v} value={v}>
                {EDUCATION_STATUS_LABEL[v]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {highSchool ? null : (
        <>
          <Field label="전공" htmlFor="ed-major">
            <Input id="ed-major" value={d.major} onChange={(ev) => set({ major: ev.target.value })} maxLength={100} />
          </Field>
          <div className="grid grid-cols-[minmax(0,1fr)_8rem] gap-3">
            <Field label="복수·부전공 (선택)" htmlFor="ed-extra">
              <Input
                id="ed-extra"
                value={d.extra_major}
                onChange={(ev) => set({ extra_major: ev.target.value })}
                maxLength={100}
              />
            </Field>
            <Field label="구분" htmlFor="ed-extra-kind">
              <Select
                id="ed-extra-kind"
                value={d.extra_major_kind}
                disabled={!d.extra_major.trim()}
                onChange={(ev) => set({ extra_major_kind: ev.target.value as ExtraMajorKind | "" })}
              >
                <option value="">고르기</option>
                {(["double", "minor"] as const).map((v) => (
                  <option key={v} value={v}>
                    {EXTRA_MAJOR_LABEL[v]}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </>
      )}
      <div className="grid grid-cols-2 gap-3">
        <Field label="입학" htmlFor="ed-in">
          <Input
            id="ed-in"
            type="month"
            value={d.entered_on}
            onChange={(ev) => set({ entered_on: ev.target.value })}
            className="tabular-nums"
          />
        </Field>
        <Field label={d.status === "expected" ? "졸업 예정" : "졸업"} htmlFor="ed-out">
          <Input
            id="ed-out"
            type="month"
            value={d.graduated_on}
            onChange={(ev) => set({ graduated_on: ev.target.value })}
            className="tabular-nums"
          />
        </Field>
      </div>
      <label className="flex min-h-9 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={d.transferred}
          onChange={(ev) => set({ transferred: ev.target.checked })}
          className="size-4 accent-[var(--app-fg)]"
        />
        편입
      </label>
      <div className="grid grid-cols-2 gap-3">
        <Field label="학점" htmlFor="ed-gpa">
          <Input
            id="ed-gpa"
            inputMode="decimal"
            value={d.gpa}
            onChange={(ev) => set({ gpa: ev.target.value })}
            placeholder="3.82"
            className="tabular-nums"
          />
        </Field>
        <Field label="만점" htmlFor="ed-scale">
          <Select id="ed-scale" value={d.gpa_scale} onChange={(ev) => set({ gpa_scale: ev.target.value })}>
            {GPA_SCALES.map((s) => (
              <option key={s} value={String(s)}>
                {s === 100 ? "100" : s.toFixed(1)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="증빙 서류" htmlFor="ed-source">
        <Select id="ed-source" value={d.source_id} onChange={(ev) => set({ source_id: ev.target.value })}>
          <option value="">없음</option>
          {(proofs.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="이어진 경험" htmlFor="ed-exp">
        <Select id="ed-exp" value={d.experience_id} onChange={(ev) => set({ experience_id: ev.target.value })}>
          <option value="">없음</option>
          {(experiences.data ?? []).map((x) => (
            <option key={x.id} value={x.id}>
              {x.title}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="메모" htmlFor="ed-note">
        <Textarea id="ed-note" value={d.note} onChange={(ev) => set({ note: ev.target.value })} maxLength={1000} />
      </Field>

      {issue && (d.school || d.gpa) ? <p className="text-[13px] text-muted">{issue}</p> : null}
      {save.error ? (
        <p role="alert" className="text-[13px] text-danger">
          {meErrorText(save.error) ?? errorText(save.error)}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <Button type="submit" variant="primary" disabled={issue !== null || save.isPending}>
          {e ? "저장" : "추가"}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          취소
        </Button>
        {e ? (
          <Button variant="ghost" className="ml-auto text-danger" onClick={() => setRemoving(true)}>
            삭제
          </Button>
        ) : null}
      </div>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={`"${e?.school ?? ""}" 삭제`}
        description="이 학력을 삭제합니다. 이은 증빙 서류와 경험은 그대로 남습니다."
        confirmLabel="삭제"
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </form>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <Label htmlFor={htmlFor} className="mb-1">
        {label}
      </Label>
      {children}
    </div>
  );
}
