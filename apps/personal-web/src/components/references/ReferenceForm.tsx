import type { PiiReport, Reference, ReferenceIn, ReferenceKind, ReferenceNote, ReferenceNoteIn } from "@career-brain/client";
import { Button, Input, Label, Select, Sheet, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import { lines, PII_KIND_LABEL, REFERENCE_KIND_LABEL, REFERENCE_KIND_ORDER } from "../../lib/references";
import { ErrorText } from "../common";

const PARSE_READ = new Set(["complete", "partial"]);

export interface ReferenceValues {
  kind: ReferenceKind;
  title: string;
  company_id: string;
  role: string;
  year: string;
  source_note: string;
}

const EMPTY: ReferenceValues = { kind: "accepted_essay", title: "", company_id: "", role: "", year: "", source_note: "" };

export function referenceValues(r: Reference): ReferenceValues {
  return {
    kind: r.kind,
    title: r.title,
    company_id: r.company_id ?? "",
    role: r.role ?? "",
    year: r.year ? String(r.year) : "",
    source_note: r.source_note ?? "",
  };
}

function toBody(v: ReferenceValues) {
  const year = Number(v.year.trim());
  return {
    kind: v.kind,
    title: v.title.trim(),
    company_id: v.company_id || null,
    role: v.role.trim() || null,
    year: v.year.trim() && Number.isInteger(year) ? year : null,
    source_note: v.source_note.trim() || null,
  };
}

/** 종류·제목·회사·직무·연도·출처 메모. 회사는 참고 › 회사에 있는 것 중에서 고른다. */
function MetaFields({ v, set }: { v: ReferenceValues; set: (k: keyof ReferenceValues, value: string) => void }) {
  const companies = useQuery({ queryKey: ["companies", ""], queryFn: () => api.companies("") });
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="ref-kind">종류</Label>
          <Select id="ref-kind" value={v.kind} onChange={(e) => set("kind", e.target.value)}>
            {REFERENCE_KIND_ORDER.map((k) => (
              <option key={k} value={k}>
                {REFERENCE_KIND_LABEL[k]}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ref-company">회사 (선택)</Label>
          <Select id="ref-company" value={v.company_id} onChange={(e) => set("company_id", e.target.value)}>
            <option value="">없음</option>
            {(companies.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="ref-title">제목</Label>
        <Input
          id="ref-title"
          value={v.title}
          onChange={(e) => set("title", e.target.value)}
          placeholder="예: 2025 하반기 물류 직무 합격 자기소개서"
          maxLength={300}
          required
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_7rem]">
        <div className="space-y-1.5">
          <Label htmlFor="ref-role">직무 (선택)</Label>
          <Input id="ref-role" value={v.role} onChange={(e) => set("role", e.target.value)} maxLength={100} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ref-year">연도 (선택)</Label>
          <Input
            id="ref-year"
            value={v.year}
            onChange={(e) => set("year", e.target.value.replace(/\D/g, "").slice(0, 4))}
            inputMode="numeric"
            placeholder="2025"
            className="tabular-nums"
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="ref-note">출처 메모 (선택)</Label>
        <Input
          id="ref-note"
          value={v.source_note}
          onChange={(e) => set("source_note", e.target.value)}
          placeholder="예: 취업 카페 공개 글, 선배에게 받은 글"
          maxLength={1000}
        />
      </div>
    </>
  );
}

/** 저장 전 개인정보 확인: 찾은 곳을 보여 주고 가릴지 고른다. */
function PiiStep({
  report,
  pending,
  error,
  onSave,
  onBack,
}: {
  report: PiiReport;
  pending: boolean;
  error: unknown;
  onSave: (mask: boolean) => void;
  onBack: () => void;
}) {
  const shown = report.hits.slice(0, 20);
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold">
          개인정보로 보이는 곳 <span className="font-normal tabular-nums">{report.total}</span>
        </h3>
        <p className="mt-0.5 text-[13px] text-muted">
          글쓴이의 이름·연락처·학교 같은 정보입니다. 가리면 ○○○로 바꿔 저장합니다. 찾지 못한 곳이 있을 수 있습니다.
        </p>
      </div>
      <ul className="divide-y divide-line border-y border-line">
        {shown.map((h) => (
          <li key={`${h.start}-${h.end}`} className="flex items-baseline gap-3 py-2 text-[13px]">
            <span className="w-20 shrink-0 text-muted">{PII_KIND_LABEL[h.kind]}</span>
            <span className="min-w-0 flex-1 truncate">
              {h.value} <span className="text-muted">→ {h.replacement}</span>
            </span>
          </li>
        ))}
      </ul>
      {report.total > shown.length ? (
        <p className="text-[13px] text-muted tabular-nums">외 {report.total - shown.length}곳</p>
      ) : null}
      <ErrorText error={error} />
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={pending} onClick={() => onSave(true)}>
          가리고 저장
        </Button>
        <Button disabled={pending} onClick={() => onSave(false)}>
          그대로 저장
        </Button>
        <Button variant="ghost" disabled={pending} onClick={onBack}>
          돌아가기
        </Button>
      </div>
    </div>
  );
}

/** 참고 글 추가: 붙여넣기 또는 참고 자료로 올린 파일(``sourceId``)에서. 저장 전에 개인정보를 확인한다. */
export function AddReferenceSheet({
  open,
  onClose,
  sourceId,
}: {
  open: boolean;
  onClose: () => void;
  sourceId?: string;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [v, setV] = useState<ReferenceValues>(EMPTY);
  const [text, setText] = useState("");
  const [report, setReport] = useState<PiiReport | null>(null);
  const set = (k: keyof ReferenceValues, value: string) => setV((prev) => ({ ...prev, [k]: value }));

  const source = useQuery({
    queryKey: ["source", sourceId],
    queryFn: () => api.source(sourceId!),
    enabled: open && !!sourceId,
    refetchInterval: (q) => {
      const parse = q.state.data?.versions[0]?.parse;
      return parse && PARSE_READ.has(parse.status) ? false : 2500;
    },
  });
  const parse = source.data?.versions[0]?.parse ?? null;
  // 올린 자료에서 만들 때는 자료 제목을 먼저 채운다
  const sourceTitle = source.data?.title;
  useEffect(() => {
    if (sourceTitle) setV((cur) => (cur.title ? cur : { ...cur, title: sourceTitle.slice(0, 300) }));
  }, [sourceTitle]);
  const sourceReady = !sourceId || (!!parse && PARSE_READ.has(parse.status));
  const fromSource = sourceId ? { source_id: sourceId } : { text };

  const reset = () => {
    setV(EMPTY);
    setText("");
    setReport(null);
    check.reset();
    create.reset();
  };
  const close = () => {
    reset();
    onClose();
  };
  const create = useMutation({
    mutationFn: (mask: boolean) =>
      api.createReference({ ...toBody(v), ...fromSource, mask_pii: mask } as ReferenceIn),
    onSuccess: ({ id }) => {
      void qc.invalidateQueries({ queryKey: ["references"] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
      close();
      navigate({ to: "/brain/references/$referenceId", params: { referenceId: id } });
    },
  });
  const check = useMutation({
    mutationFn: () => api.referencePiiCheck(fromSource),
    onSuccess: (r) => (r.total ? setReport(r) : create.mutate(false)),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (v.title.trim() && (sourceId ? sourceReady : text.trim())) check.mutate();
  };
  const busy = check.isPending || create.isPending;

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && close()}
      title="참고 글 추가"
      description="다른 사람의 글을 내 경험과 따로 보관합니다. 문서 근거로 쓰지 않습니다."
    >
      {report ? (
        <PiiStep
          report={report}
          pending={create.isPending}
          error={create.error}
          onSave={(mask) => create.mutate(mask)}
          onBack={() => setReport(null)}
        />
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <MetaFields v={v} set={set} />
          {sourceId ? (
            <div className="space-y-1 border-t border-line pt-3 text-[13px]">
              <div className="font-medium">{source.data?.title ?? "참고 자료"}</div>
              <div className="flex items-center gap-2 text-muted">
                {sourceReady ? (
                  "받은 자료의 글로 만듭니다. 원본 파일은 그대로 남습니다."
                ) : (
                  <>
                    <Spinner /> 자료를 읽는 중입니다. 끝나면 저장할 수 있습니다.
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="ref-text">글</Label>
              <Textarea
                id="ref-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={10}
                maxLength={200000}
                required
              />
              <p className="text-[13px] text-muted">저장하기 전에 글 속 이름·연락처·학교를 찾아 보여 줍니다.</p>
            </div>
          )}
          <ErrorText error={check.error ?? create.error} />
          <div className="flex gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={busy || !v.title.trim() || (sourceId ? !sourceReady : !text.trim())}
            >
              {busy ? <Spinner /> : null} 저장
            </Button>
            <Button variant="ghost" onClick={close}>
              취소
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}

/** 분류 칸 수정 (글은 바꾸지 않는다). */
export function EditReferenceSheet({ r, open, onClose }: { r: Reference; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [v, setV] = useState<ReferenceValues>(() => referenceValues(r));
  const set = (k: keyof ReferenceValues, value: string) => setV((prev) => ({ ...prev, [k]: value }));
  const save = useMutation({
    mutationFn: () => api.updateReference(r.id, toBody(v)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reference", r.id] });
      void qc.invalidateQueries({ queryKey: ["references"] });
      onClose();
    },
  });
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          save.reset();
          onClose();
        }
      }}
      title="참고 글 수정"
    >
      {open ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (v.title.trim()) save.mutate();
          }}
          className="space-y-4"
        >
          <MetaFields v={v} set={set} />
          <ErrorText error={save.error} />
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={save.isPending || !v.title.trim()}>
              저장
            </Button>
            <Button variant="ghost" onClick={onClose}>
              취소
            </Button>
          </div>
        </form>
      ) : null}
    </Sheet>
  );
}

// ---------------------------------------------------------------- 구조 메모

export interface NoteValues {
  question: string;
  structure: string;
  materials: string;
  competencies: string;
}

export const EMPTY_NOTE: NoteValues = { question: "", structure: "", materials: "", competencies: "" };

export function noteValues(n: ReferenceNote): NoteValues {
  return {
    question: n.question ?? "",
    structure: n.structure.join("\n"),
    materials: n.materials.join("\n"),
    competencies: n.competencies.join("\n"),
  };
}

export function toNoteBody(v: NoteValues): ReferenceNoteIn {
  return {
    question: v.question.trim() || null,
    structure: lines(v.structure),
    materials: lines(v.materials),
    competencies: lines(v.competencies),
  };
}

/** 문항·문단 구성·소재 종류·강조 역량. 원문 문장이 아니라 구성을 적는다. */
export function NoteForm({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial: NoteValues;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (v: NoteValues) => void;
  onCancel: () => void;
}) {
  const [v, setV] = useState(initial);
  const set = (k: keyof NoteValues) => (e: { target: { value: string } }) =>
    setV((prev) => ({ ...prev, [k]: e.target.value }));
  const empty = !(v.question.trim() || lines(v.structure).length || lines(v.materials).length || lines(v.competencies).length);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!empty) onSubmit(v);
      }}
      className="space-y-4"
    >
      <p className="text-[13px] text-muted">원문 문장은 옮기지 않습니다. 글이 어떻게 짜였는지만 적습니다.</p>
      <div className="space-y-1.5">
        <Label htmlFor="note-question">문항 (선택)</Label>
        <Input
          id="note-question"
          value={v.question}
          onChange={set("question")}
          placeholder="예: 지원 동기와 입사 후 포부"
          maxLength={1000}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="note-structure">문단 구성</Label>
        <Textarea
          id="note-structure"
          value={v.structure}
          onChange={set("structure")}
          rows={4}
          placeholder={"두괄식 결론\n상황\n본인 행동\n수치 결과"}
        />
        <p className="text-[13px] text-muted">한 줄에 문단 하나입니다.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="note-materials">소재 종류</Label>
          <Textarea
            id="note-materials"
            value={v.materials}
            onChange={set("materials")}
            rows={3}
            placeholder={"동아리 운영\n인턴 프로젝트"}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="note-competencies">강조 역량</Label>
          <Textarea
            id="note-competencies"
            value={v.competencies}
            onChange={set("competencies")}
            rows={3}
            placeholder={"문제 해결\n협업"}
          />
        </div>
      </div>
      <ErrorText error={error} />
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={pending || empty}>
          {submitLabel}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          취소
        </Button>
      </div>
    </form>
  );
}
