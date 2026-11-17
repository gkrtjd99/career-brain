import type { PiiReport, Reference, ReferenceNote } from "@career-brain/client";
import { Button, ConfirmDialog, Section, Sheet, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import {
  EditReferenceSheet,
  EMPTY_NOTE,
  NoteForm,
  noteValues,
  toNoteBody,
  type NoteValues,
} from "../components/references/ReferenceForm";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { DATA_CLASS_LABEL } from "../lib/labels";
import { OTHERS_WRITING, PII_KIND_LABEL, referenceMeta, suggestNoteLabel } from "../lib/references";

const route = getRouteApi("/brain/references/$referenceId");

export function ReferencePage() {
  const { referenceId } = route.useParams();
  const q = useQuery({ queryKey: ["reference", referenceId], queryFn: () => api.reference(referenceId) });
  return <Loaded query={q}>{(r) => <ReferenceView r={r} />}</Loaded>;
}

function useInvalidate(referenceId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["reference", referenceId] });
    void qc.invalidateQueries({ queryKey: ["references"] });
  };
}

function ReferenceView({ r }: { r: Reference }) {
  const invalidate = useInvalidate(r.id);
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [suggestNotes, setSuggestNotes] = useState<string[] | null>(null);
  const candidates = r.notes.filter((n) => n.status === "candidate");
  const confirmed = r.notes.filter((n) => n.status === "confirmed");
  const suggest = useMutation({
    mutationFn: () => api.suggestReferenceNotes(r.id),
    onSuccess: (res) => {
      setSuggestNotes(res.notes);
      invalidate();
    },
  });
  const add = useMutation({
    mutationFn: (v: NoteValues) => api.addReferenceNote(r.id, toNoteBody(v)),
    onSuccess: () => {
      invalidate();
      setAdding(false);
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteReference(r.id),
    onSuccess: () => {
      invalidate();
      navigate({ to: "/brain/references" });
    },
  });
  const meta = referenceMeta(r);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        back={{ to: "/brain/references", label: "참고 글" }}
        title={r.title}
        subtitle={meta}
        actions={
          <>
            <Button onClick={() => setEditing(true)}>
              <Pencil size={14} /> 수정
            </Button>
            <Button variant="ghost" size="icon" aria-label="참고 글 삭제" onClick={() => setDeleting(true)}>
              <Trash2 size={14} />
            </Button>
          </>
        }
      />
      <p className="-mt-2 mb-8 border-y border-line py-2.5 text-[13px] font-medium">{OTHERS_WRITING}</p>

      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-14">
        <div className="min-w-0 space-y-10">
          <section>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h2 className="text-[15px] font-semibold">구조 메모</h2>
              <div className="flex items-center gap-1">
                <Button size="sm" variant="ghost" onClick={() => suggest.mutate()} disabled={suggest.isPending}>
                  {suggest.isPending ? <Spinner /> : <Sparkles size={14} />} 메모 후보 만들기
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
                  <Plus size={14} /> 메모 추가
                </Button>
              </div>
            </div>
            <p className="mt-0.5 text-[13px] text-muted">
              문항·문단 구성·소재 종류·강조 역량을 적습니다. 지원 준비와 초안에는 원문 대신 확인한 메모만 씁니다.
            </p>
            {suggestNotes?.length ? (
              <ul className="mt-2 space-y-0.5 text-[13px] text-muted">
                {suggestNotes.map((n) => (
                  <li key={n}>{suggestNoteLabel(n)}</li>
                ))}
              </ul>
            ) : null}
            <ErrorText error={suggest.error} className="mt-2" />

            {candidates.length ? (
              <div className="mt-6">
                <h3 className="text-sm font-semibold">
                  확인할 메모 <span className="font-normal text-muted tabular-nums">{candidates.length}</span>
                </h3>
                <p className="mt-0.5 text-[13px] text-muted">모델이 만든 후보입니다. 글과 맞으면 확인합니다.</p>
                <NoteList notes={candidates} referenceId={r.id} />
              </div>
            ) : null}
            {confirmed.length ? (
              <NoteList notes={confirmed} referenceId={r.id} />
            ) : !candidates.length ? (
              <p className="mt-3 border-t border-line pt-3 text-sm text-muted">
                아직 메모가 없습니다. 모델이 연결되어 있으면 메모 후보를 만들 수 있고, 직접 쓸 수도 있습니다.
              </p>
            ) : null}
          </section>
        </div>

        <div className="mt-10 min-w-0 space-y-10 lg:mt-0">
          <Section title="원문">
            <OriginalText r={r} />
          </Section>
          <Section title="출처">
            <dl className="divide-y divide-line border-y border-line text-[13px]">
              {r.source_note ? <Row label="메모">{r.source_note}</Row> : null}
              {r.source_id ? (
                <Row label="자료">
                  <Link
                    to="/brain/sources/$sourceId"
                    params={{ sourceId: r.source_id }}
                    className="underline underline-offset-2 hover:text-fg"
                  >
                    {r.source_title ?? "참고 자료"}
                  </Link>
                </Row>
              ) : null}
              <Row label="자료 등급">{DATA_CLASS_LABEL[r.data_class] ?? r.data_class}</Row>
              <Row label="추가한 날">{formatDate(r.created_at)}</Row>
            </dl>
          </Section>
        </div>
      </div>

      <EditReferenceSheet r={r} open={editing} onClose={() => setEditing(false)} />
      <Sheet open={adding} onOpenChange={(o) => !o && setAdding(false)} title="메모 추가">
        {adding ? (
          <NoteForm
            initial={EMPTY_NOTE}
            submitLabel="저장"
            pending={add.isPending}
            error={add.error}
            onSubmit={(v) => add.mutate(v)}
            onCancel={() => setAdding(false)}
          />
        ) : null}
      </Sheet>
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`${r.title} 삭제`}
        description="참고 글과 구조 메모를 삭제합니다. 올린 자료는 자료 목록에 남습니다. 되돌릴 수 없습니다."
        confirmLabel={remove.isPending ? "삭제하는 중" : "삭제"}
        busy={remove.isPending}
        tone="danger"
        onConfirm={() => remove.mutate()}
      >
        <ErrorText error={remove.error} />
      </ConfirmDialog>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-2">
      <dt className="w-20 shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 flex-1 break-words">{children}</dd>
    </div>
  );
}

/** 원문: 회색으로, 옮겨 쓰지 않는 글. 개인정보가 남아 있으면 지금 가릴 수 있다. */
function OriginalText({ r }: { r: Reference }) {
  const invalidate = useInvalidate(r.id);
  const [report, setReport] = useState<PiiReport | null>(null);
  const check = useMutation({ mutationFn: () => api.referencePiiCheck({ text: r.text }), onSuccess: setReport });
  const mask = useMutation({
    mutationFn: () => api.updateReference(r.id, { mask_pii: true }),
    onSuccess: () => {
      setReport(null);
      invalidate();
    },
  });
  return (
    <div>
      <div className="max-h-[32rem] overflow-y-auto border-y border-line py-3 text-[13px] leading-6 break-words whitespace-pre-wrap text-muted">
        {r.text}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted tabular-nums">
        <span>{r.text.length.toLocaleString()}자</span>
        {r.pii_masked ? <span>개인정보 {r.pii_masked}곳을 가렸습니다.</span> : null}
        {!report ? (
          <button
            type="button"
            onClick={() => check.mutate()}
            disabled={check.isPending}
            className="inline-flex min-h-9 items-center underline underline-offset-2 hover:text-fg sm:min-h-0"
          >
            개인정보 찾기
          </button>
        ) : null}
      </div>
      {report ? (
        report.total ? (
          <div className="mt-2 space-y-2 text-[13px]">
            <p>
              개인정보로 보이는 곳 <span className="tabular-nums">{report.total}</span>개:{" "}
              <span className="text-muted">
                {Object.entries(report.by_kind)
                  .map(([k, n]) => `${PII_KIND_LABEL[k as keyof typeof PII_KIND_LABEL] ?? k} ${n}`)
                  .join(", ")}
              </span>
            </p>
            <div className="flex gap-2">
              <Button size="sm" variant="primary" onClick={() => mask.mutate()} disabled={mask.isPending}>
                ○○○로 가리기
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setReport(null)}>
                취소
              </Button>
            </div>
          </div>
        ) : (
          <p className="mt-2 text-[13px] text-muted">찾은 개인정보가 없습니다.</p>
        )
      ) : null}
      <ErrorText error={check.error ?? mask.error} className="mt-1" />
    </div>
  );
}

function NoteList({ notes, referenceId }: { notes: ReferenceNote[]; referenceId: string }) {
  return (
    <ul className="mt-3 divide-y divide-line border-y border-line">
      {notes.map((n) => (
        <NoteItem key={n.id} n={n} referenceId={referenceId} />
      ))}
    </ul>
  );
}

function NoteItem({ n, referenceId }: { n: ReferenceNote; referenceId: string }) {
  const invalidate = useInvalidate(referenceId);
  const [editing, setEditing] = useState(false);
  const confirm = useMutation({
    mutationFn: () => api.confirmReferenceNote(referenceId, n.id),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: () => api.deleteReferenceNote(referenceId, n.id), onSuccess: invalidate });
  const save = useMutation({
    mutationFn: (v: NoteValues) => api.updateReferenceNote(referenceId, n.id, toNoteBody(v)),
    onSuccess: () => {
      invalidate();
      setEditing(false);
    },
  });
  const rows: [string, string][] = [
    ["문단 구성", n.structure.join(" → ")],
    ["소재", n.materials.join(", ")],
    ["강조 역량", n.competencies.join(", ")],
  ];
  return (
    <li className="py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {n.question ? <p className="text-sm leading-6 font-medium break-words">{n.question}</p> : null}
          <dl className="mt-0.5 space-y-0.5 text-[13px] leading-5">
            {rows
              .filter(([, v]) => v)
              .map(([label, v]) => (
                <div key={label} className="flex gap-3">
                  <dt className="w-16 shrink-0 text-muted">{label}</dt>
                  <dd className="min-w-0 flex-1 break-words">{v}</dd>
                </div>
              ))}
          </dl>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {n.status === "candidate" ? (
            <Button size="sm" className="max-sm:h-9" onClick={() => confirm.mutate()} disabled={confirm.isPending}>
              확인
            </Button>
          ) : null}
          <Button size="icon" variant="ghost" aria-label="메모 수정" onClick={() => setEditing(true)}>
            <Pencil size={14} />
          </Button>
          <Button size="icon" variant="ghost" aria-label="메모 삭제" onClick={() => remove.mutate()} disabled={remove.isPending}>
            <Trash2 size={14} />
          </Button>
        </div>
      </div>
      <ErrorText error={confirm.error ?? remove.error} className="mt-1" />
      <Sheet open={editing} onOpenChange={(o) => !o && setEditing(false)} title="메모 수정">
        {editing ? (
          <NoteForm
            initial={noteValues(n)}
            submitLabel="저장"
            pending={save.isPending}
            error={save.error}
            onSubmit={(v) => save.mutate(v)}
            onCancel={() => setEditing(false)}
          />
        ) : null}
      </Sheet>
    </li>
  );
}
