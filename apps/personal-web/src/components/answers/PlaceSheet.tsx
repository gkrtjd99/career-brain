import type { PlaceIn, PlaceResult } from "@career-brain/client";
import { Button, Input, Label, Select, SelectItem, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { api } from "../../lib/api";
import { meErrorText } from "../../lib/brainMe";
import { errorText, Loaded } from "../common";

const NEW = "__new__";

/**
 * 공통 답변·지난 제출본의 글을 작성 중인 지원 문서에 넣는다.
 * 문서를 고르면 그 끝에 문단으로 더하고, "새 문서"면 문항 문구로 새 문서를 만든다.
 * 넣기 전에 dry_run으로 글에 보이는 다른 회사 이름을 알린다 (막지 않는다).
 */
export function PlaceSheet({
  open,
  onClose,
  title,
  defaultPrompt,
  previewKey,
  place,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  defaultPrompt?: string | null;
  /** 미리 보기 캐시 이름 (넣을 글마다 다르게) */
  previewKey: string;
  place: (body: PlaceIn) => Promise<PlaceResult>;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description="작성 중인 지원 문서 끝에 문단으로 더합니다. 넣은 뒤에는 그 지원에 맞게 고칩니다."
    >
      {open ? <PlaceForm defaultPrompt={defaultPrompt ?? ""} previewKey={previewKey} place={place} onDone={onClose} /> : null}
    </Sheet>
  );
}

function PlaceForm({
  defaultPrompt,
  previewKey,
  place,
  onDone,
}: {
  defaultPrompt: string;
  previewKey: string;
  place: (body: PlaceIn) => Promise<PlaceResult>;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const [appId, setAppId] = useState("");
  const [docId, setDocId] = useState("");
  const [prompt, setPrompt] = useState(defaultPrompt);
  const app = useQuery({
    queryKey: ["application", appId],
    queryFn: () => api.application(appId),
    enabled: appId !== "",
  });
  const doc = app.data?.documents.find((d) => d.id === docId);
  const body = (dry: boolean): PlaceIn =>
    docId === NEW
      ? { application_id: appId, question_prompt: prompt.trim() || null, dry_run: dry }
      : { application_id: appId, document_id: docId, base_revision: doc?.current_revision ?? 0, dry_run: dry };
  const ready = appId !== "" && docId !== "" && (docId === NEW || doc !== undefined);
  // 미리 보기: 다른 회사 이름
  const preview = useQuery({
    queryKey: ["place-preview", previewKey, appId, docId, doc?.current_revision ?? null],
    queryFn: () => place(body(true)),
    enabled: ready,
    staleTime: 10_000,
  });
  const run = useMutation({
    mutationFn: () => place(body(false)),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["application", r.application_id] });
      void qc.invalidateQueries({ queryKey: ["answer-assets"] });
      void qc.invalidateQueries({ queryKey: ["place-preview"] });
      onDone();
      if (r.document_id)
        void navigate({
          to: "/applications/$applicationId/documents/$documentId",
          params: { applicationId: r.application_id, documentId: r.document_id },
        });
    },
  });
  const mentions = preview.data?.company_mentions ?? [];
  const dropped = preview.data?.claims_dropped ?? 0;

  return (
    <Loaded query={apps}>
      {(list) => {
        const drafts = list.filter((a) => a.status === "draft");
        if (!drafts.length)
          return (
            <p className="border-t border-line pt-3 text-sm text-muted">
              작성 중인 지원이 없습니다. 지원을 먼저 만들면 여기에서 넣을 수 있습니다.
            </p>
          );
        return (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (ready) run.mutate();
            }}
          >
            <div>
              <Label htmlFor="place-app" className="mb-1">
                지원
              </Label>
              <Select
                id="place-app"
                value={appId}
                onValueChange={(v) => {
                  setAppId(v);
                  setDocId("");
                }}
              >
                <SelectItem value="">고르기</SelectItem>
                {drafts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.title}
                  </SelectItem>
                ))}
              </Select>
            </div>
            {appId ? (
              <div>
                <Label htmlFor="place-doc" className="mb-1">
                  문서
                </Label>
                <Select id="place-doc" value={docId} onValueChange={(v) => setDocId(v)}>
                  <SelectItem value="">고르기</SelectItem>
                  {(app.data?.documents ?? []).map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.title}
                      {d.current_revision === 0 ? " (비어 있음)" : ""}
                    </SelectItem>
                  ))}
                  <SelectItem value={NEW}>새 문서로 넣기</SelectItem>
                </Select>
              </div>
            ) : null}
            {docId === NEW ? (
              <div>
                <Label htmlFor="place-prompt" className="mb-1">
                  문항 (선택)
                </Label>
                <Input
                  id="place-prompt"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  maxLength={2000}
                  placeholder="예: 지원 동기를 작성해 주세요. (700자)"
                />
                <p className="mt-1 text-xs text-muted">문항을 적으면 공고 문항 문서로 만들고, 괄호의 글자 수를 제한으로 씁니다.</p>
              </div>
            ) : null}

            {ready && mentions.length ? (
              <p role="status" className="text-[13px]">
                <span className="font-semibold">다른 회사 이름이 보입니다: {mentions.join(", ")}.</span>{" "}
                <span className="text-muted">넣은 뒤 이 지원에 맞게 고쳐 주세요.</span>
              </p>
            ) : null}
            {ready && dropped ? (
              <p className="text-[13px] text-muted">
                더는 확인한 사실이 아닌 근거 {dropped}개는 붙이지 않습니다.
              </p>
            ) : null}
            {run.error ? (
              <p role="alert" className="text-[13px] text-danger">
                {meErrorText(run.error) ?? errorText(run.error)}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
              <Button type="submit" variant="primary" disabled={!ready || run.isPending}>
                {mentions.length ? "그래도 넣기" : "넣기"}
              </Button>
              <Button variant="ghost" onClick={onDone}>
                취소
              </Button>
            </div>
          </form>
        );
      }}
    </Loaded>
  );
}
