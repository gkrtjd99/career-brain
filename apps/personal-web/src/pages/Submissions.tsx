import { ANSWER_ASSET_KINDS, type AnswerAssetKind, type SubmittedDocument } from "@career-brain/client";
import { Button, Input, Label, Select } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { PlaceSheet } from "../components/answers/PlaceSheet";
import { errorText, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { ANSWER_KIND_LABEL, charsText, meErrorText } from "../lib/brainMe";
import { formatDate } from "../lib/dates";
import { DOC_KIND } from "../lib/labels";

const ROW = "-mx-2 block rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const message = (e: unknown) => meErrorText(e) ?? errorText(e);

/** 브레인 › 나 › 지난 제출본: 제출할 때 고정한 글. 읽기만 하고, 새 글은 "이 글로 시작"으로 만든다. */
export function SubmissionsPage() {
  const q = useQuery({ queryKey: ["submitted-documents"], queryFn: api.submittedDocuments });
  return (
    <div className="max-w-3xl">
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="지난 제출본"
        subtitle="제출할 때 고정한 글입니다. 고칠 수 없고, 이 글로 새 답변이나 다른 지원의 글을 시작할 수 있습니다."
      />
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((d) => (
                <SubmissionRow key={`${d.submission_id}-${d.document_id}`} d={d} />
              ))}
            </ul>
          ) : (
            <p className="border-t border-line pt-3 text-sm text-muted">
              아직 제출한 지원이 없습니다. 지원을 제출하면 그때의 글이 여기에 남습니다.
            </p>
          )
        }
      </Loaded>
    </div>
  );
}

function SubmissionRow({ d }: { d: SubmittedDocument }) {
  return (
    <li>
      <Link
        to="/brain/submissions/$submissionId/$documentId"
        params={{ submissionId: d.submission_id, documentId: d.document_id }}
        className={ROW}
      >
        <div className="truncate text-sm font-medium">{d.question ?? d.title}</div>
        <p className="mt-0.5 truncate text-[13px] text-muted tabular-nums">
          {[d.company ?? d.application_title, d.role, charsText(d.chars, d.char_limit), `제출 ${formatDate(d.submitted_at)}`]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </Link>
    </li>
  );
}

const detailRoute = getRouteApi("/brain/submissions/$submissionId/$documentId");

/** 제출본 한 편: 그때의 글과 근거. 회사 이름이 보이면 다른 지원에 쓸 때 알린다. */
export function SubmissionPage() {
  const { submissionId, documentId } = detailRoute.useParams();
  const q = useQuery({
    queryKey: ["submitted-documents", submissionId, documentId],
    queryFn: () => api.submittedDocument(submissionId, documentId),
  });
  const [placing, setPlacing] = useState(false);
  return (
    <div className="max-w-3xl">
      <Loaded query={q}>
        {(d) => (
          <>
            <PageHeader
              back={{ to: "/brain/submissions", label: "지난 제출본" }}
              title={d.question ?? d.title}
              subtitle={[
                d.company ?? d.application_title,
                d.role,
                DOC_KIND[d.kind as keyof typeof DOC_KIND] ?? null,
                `제출 ${formatDate(d.submitted_at)}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            />
            <div className="space-y-10">
              <section aria-label="제출한 글">
                <div className="mb-2 flex items-baseline justify-between gap-2 text-[13px] text-muted tabular-nums">
                  <span>읽기 전용</span>
                  <span>{charsText(d.chars, d.char_limit)}</span>
                </div>
                <div className="space-y-4 border-y border-line py-4 text-[15px] leading-7 whitespace-pre-wrap">
                  {d.blocks.length ? d.blocks.map((b, i) => <p key={b.id ?? i}>{b.text}</p>) : <p>{d.text}</p>}
                </div>
              </section>

              <section aria-labelledby="sub-evidence">
                <h2 id="sub-evidence" className="text-[15px] font-semibold">
                  제출 때의 근거 <span className="font-normal text-muted tabular-nums">{d.evidence.length}</span>
                </h2>
                {d.evidence.length ? (
                  <ul className="mt-2 divide-y divide-line border-y border-line">
                    {d.evidence.map((e) => (
                      <li key={e.claim_id} className="py-2.5">
                        <p className="text-sm leading-6">{e.statement}</p>
                        {e.experience?.title ? (
                          <p className="text-[13px] text-muted">{e.experience.title}</p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-[13px] text-muted">이은 근거 없이 제출했습니다.</p>
                )}
              </section>

              <section aria-labelledby="sub-start" className="space-y-4">
                <h2 id="sub-start" className="text-[15px] font-semibold">
                  이 글로 시작
                </h2>
                {d.company_mentions.length ? (
                  <p role="status" className="text-[13px]">
                    <span className="font-semibold">글에 회사 이름이 보입니다: {d.company_mentions.join(", ")}.</span>{" "}
                    <span className="text-muted">다른 지원에 쓸 때는 고쳐 주세요.</span>
                  </p>
                ) : null}
                <StartAnswer submissionId={submissionId} documentId={documentId} defaultTitle={d.question ?? d.title} />
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
                  <Button onClick={() => setPlacing(true)}>다른 지원 문서에 넣기</Button>
                </div>
                {d.answers.length ? (
                  <p className="text-[13px] text-muted">
                    이 글에서 시작한 답변:{" "}
                    {d.answers.map((a, i) => (
                      <span key={a.id}>
                        {i ? ", " : ""}
                        <Link
                          to="/brain/answers/$answerId"
                          params={{ answerId: a.id }}
                          className="text-fg underline underline-offset-2"
                        >
                          {a.title}
                        </Link>
                      </span>
                    ))}
                  </p>
                ) : null}
              </section>
            </div>
            <PlaceSheet
              open={placing}
              onClose={() => setPlacing(false)}
              title="다른 지원 문서에 넣기"
              defaultPrompt={d.question}
              previewKey={`submission:${submissionId}:${documentId}`}
              place={(body) => api.useSubmittedDocument(submissionId, documentId, body)}
            />
          </>
        )}
      </Loaded>
    </div>
  );
}

function StartAnswer({
  submissionId,
  documentId,
  defaultTitle,
}: {
  submissionId: string;
  documentId: string;
  defaultTitle: string;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [kind, setKind] = useState<AnswerAssetKind>("motivation");
  const [title, setTitle] = useState(defaultTitle.slice(0, 200));
  const start = useMutation({
    mutationFn: () =>
      api.startAnswerFromSubmission(submissionId, documentId, { kind, title: title.trim() || null }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["answer-assets"] });
      void qc.invalidateQueries({ queryKey: ["submitted-documents", submissionId, documentId] });
      void navigate({ to: "/brain/answers/$answerId", params: { answerId: r.answer_id } });
    },
  });
  return (
    <form
      className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)_auto] sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        start.mutate();
      }}
    >
      <div>
        <Label htmlFor="sa-kind" className="mb-1">
          종류
        </Label>
        <Select id="sa-kind" value={kind} onChange={(e) => setKind(e.target.value as AnswerAssetKind)}>
          {ANSWER_ASSET_KINDS.map((k) => (
            <option key={k} value={k}>
              {ANSWER_KIND_LABEL[k]}
            </option>
          ))}
        </Select>
      </div>
      <div className="min-w-0">
        <Label htmlFor="sa-title" className="mb-1">
          제목
        </Label>
        <Input id="sa-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
      </div>
      <Button type="submit" variant="primary" disabled={start.isPending}>
        새 공통 답변으로
      </Button>
      {start.error ? (
        <p role="alert" className="text-[13px] text-danger sm:col-span-3">
          {message(start.error)}
        </p>
      ) : null}
    </form>
  );
}
