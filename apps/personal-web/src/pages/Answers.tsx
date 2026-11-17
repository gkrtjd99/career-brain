import { ANSWER_ASSET_KINDS, type AnswerAssetKind, type AnswerAssetSummary } from "@career-brain/client";
import { Button, cn, Input, Label, Select, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { ANSWER_KIND_LABEL, charsText, meErrorText } from "../lib/brainMe";
import { formatDate } from "../lib/dates";

const ROW = "-mx-2 block rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const CHIP =
  "inline-flex h-9 items-center rounded-full px-3.5 text-[13px] tabular-nums transition-colors duration-150 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** 브레인 › 나 › 공통 답변: 지원마다 다시 쓰는 내 글. 근거는 확인한 사실만 잇는다. */
export function AnswersPage() {
  const q = useQuery({ queryKey: ["answer-assets"], queryFn: api.answerAssets });
  const [kind, setKind] = useState<AnswerAssetKind | undefined>(undefined);
  const [adding, setAdding] = useState(false);
  return (
    <div className="max-w-3xl">
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="공통 답변"
        subtitle="1분 자기소개·지원 동기 틀처럼 여러 지원에 다시 쓰는 글입니다. 지원서에 넣으면 쓴 곳이 남습니다."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus size={15} /> 답변 추가
          </Button>
        }
      />
      <Loaded query={q}>
        {(rows) => {
          if (!rows.length)
            return (
              <p className="border-t border-line pt-3 text-sm text-muted">
                아직 공통 답변이 없습니다. 새로 쓰거나{" "}
                <Link to="/brain/submissions" className="text-fg underline underline-offset-2">
                  지난 제출본
                </Link>
                에서 시작할 수 있습니다.
              </p>
            );
          const kinds = ANSWER_ASSET_KINDS.filter((k) => rows.some((r) => r.kind === k));
          const shown = kind ? rows.filter((r) => r.kind === kind) : rows;
          return (
            <section>
              {kinds.length > 1 ? (
                <div className="mb-3 flex flex-wrap items-center gap-1" role="radiogroup" aria-label="종류 필터">
                  {[undefined, ...kinds].map((k) => (
                    <button
                      key={k ?? "all"}
                      type="button"
                      role="radio"
                      aria-checked={kind === k}
                      onClick={() => setKind(k)}
                      className={cn(
                        CHIP,
                        kind === k ? "bg-fg font-medium text-bg" : "text-muted hover:bg-subtle hover:text-fg",
                      )}
                    >
                      {k ? ANSWER_KIND_LABEL[k] : "전체"}
                    </button>
                  ))}
                </div>
              ) : null}
              <ul className="divide-y divide-line border-y border-line">
                {shown.map((a) => (
                  <AnswerRow key={a.id} a={a} />
                ))}
              </ul>
            </section>
          );
        }}
      </Loaded>
      <NewAnswerSheet open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

function AnswerRow({ a }: { a: AnswerAssetSummary }) {
  return (
    <li>
      <Link to="/brain/answers/$answerId" params={{ answerId: a.id }} className={ROW}>
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-sm font-medium">{a.title}</span>
          <span className="shrink-0 text-[13px] text-muted max-sm:hidden">{ANSWER_KIND_LABEL[a.kind]}</span>
        </div>
        {a.excerpt ? <p className="mt-0.5 truncate text-[13px] text-muted max-sm:hidden">{a.excerpt}</p> : null}
        <p className="mt-0.5 truncate text-[13px] text-muted tabular-nums">
          <span className="sm:hidden">{ANSWER_KIND_LABEL[a.kind]} · </span>
          {[
            charsText(a.chars),
            a.claims ? `근거 ${a.claims}` : "근거 없음",
            a.uses ? `쓴 곳 ${a.uses}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
          <span className="max-sm:hidden"> · 고친 날 {formatDate(a.updated_at)}</span>
        </p>
      </Link>
    </li>
  );
}

function NewAnswerSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [kind, setKind] = useState<AnswerAssetKind>("intro");
  const [title, setTitle] = useState("");
  const create = useMutation({
    mutationFn: () => api.createAnswerAsset({ kind, title: title.trim() || ANSWER_KIND_LABEL[kind] }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["answer-assets"] });
      onClose();
      setTitle("");
      void navigate({ to: "/brain/answers/$answerId", params: { answerId: r.id } });
    },
  });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="공통 답변 추가">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div>
          <Label htmlFor="na-kind" className="mb-1">
            종류
          </Label>
          <Select id="na-kind" value={kind} onChange={(e) => setKind(e.target.value as AnswerAssetKind)}>
            {ANSWER_ASSET_KINDS.map((k) => (
              <option key={k} value={k}>
                {ANSWER_KIND_LABEL[k]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="na-title" className="mb-1">
            제목
          </Label>
          <Input
            id="na-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
            placeholder={ANSWER_KIND_LABEL[kind]}
          />
        </div>
        {create.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {meErrorText(create.error) ?? errorText(create.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" disabled={create.isPending}>
            만들고 쓰기
          </Button>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
