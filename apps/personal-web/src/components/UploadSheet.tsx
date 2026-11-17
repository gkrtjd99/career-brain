import type { SourcePurpose, UploadOut } from "@career-brain/client";
import { Button, cn, Input, Label, Select, Sheet, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { PURPOSE_LABEL } from "../lib/labels";
import { ErrorText } from "./common";
import { PurposeChoice } from "./sources/PurposeChoice";

const ACCEPT =
  ".pdf,.docx,.png,.jpg,.jpeg,.txt,.md,.html,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,text/plain,text/markdown,text/html";
const PARSE_DONE = new Set(["complete", "partial", "failed"]);
const EXTRACT_DONE = new Set(["complete", "partial", "failed"]);
/** 용도 제안에 보내는 앞부분 글 (서버도 이만큼만 본다) */
const HEAD_CHARS = 2000;
const TEXT_LIKE = /\.(txt|md|html?)$/i;
const MAX_PASTE = 200_000;

const PARSE_LABEL: Record<string, string> = {
  pending: "읽는 중",
  waiting_provider: "외부 OCR 대기",
  complete: "읽기 완료",
  partial: "일부만 읽음",
  failed: "읽기 실패",
};

const UPLOAD_ERROR: Record<string, string> = {
  unsupported_type: "PDF·DOCX·PNG·JPEG·텍스트·Markdown·HTML만 올릴 수 있습니다.",
  mime_mismatch: "파일 내용과 확장자가 맞지 않습니다.",
  empty: "빈 파일입니다.",
  too_large: "파일이 너무 큽니다.",
};

type StepState = "done" | "warn" | "failed" | "running";

function stateOf(status: string): StepState {
  if (status === "complete") return "done";
  if (status === "partial") return "warn";
  if (status === "failed") return "failed";
  return "running";
}

/** 배지 대신 글자로: 실패만 빨강, 일부만 된 것은 진하게. */
const STATE_TEXT: Record<StepState, string> = {
  done: "font-medium",
  warn: "font-semibold",
  failed: "font-medium text-danger",
  running: "text-muted",
};

const CHIP =
  "inline-flex h-9 items-center rounded-full px-3.5 text-[13px] transition-colors duration-150 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** 붙여넣은 글은 텍스트 파일 하나로 올린다 (같은 저장·읽기 경로) */
function pastedFile(text: string, title: string) {
  const base = (title.trim() || "붙여넣은 글").replace(/[\\/:*?"<>|]+/g, " ").slice(0, 80);
  return new File([text], `${base}.txt`, { type: "text/plain" });
}

/** 자료 올리기 → 용도 확인 → 읽기(파싱) → (내 경험 자료면) 경험 추출 요청. 진행 상태를 그대로 보여준다. */
export function UploadSheet({
  open,
  onClose,
  purpose: initialPurpose = "experience",
}: {
  open: boolean;
  onClose: () => void;
  /** 처음 고른 용도 (증빙 서류 화면에서 열면 proof). 사람이 바꿀 수 있다 */
  purpose?: SourcePurpose;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [title, setTitle] = useState("");
  const [dataClass, setDataClass] = useState("private");
  const [purpose, setPurpose] = useState<SourcePurpose>(initialPurpose);
  // 사람이 용도를 직접 고르면 제안으로 덮지 않는다
  const [picked, setPicked] = useState(false);
  // DOCX 숨김 서식 글(w:vanish)을 원본 블록에서 뺄지 (기본은 넣고 검사가 경고)
  const [dropHidden, setDropHidden] = useState(false);
  const isDocx = mode === "file" && !!file && /\.docx$/i.test(file.name);
  const [uploaded, setUploaded] = useState<UploadOut | null>(null);
  const [extractionId, setExtractionId] = useState<string | null>(null);

  // 용도 제안: 파일 이름·형식, 텍스트 파일·붙여넣기는 앞부분 글까지 (모델 없이 서버의 규칙으로)
  const [head, setHead] = useState("");
  useEffect(() => {
    if (mode !== "file" || !file || !TEXT_LIKE.test(file.name)) {
      setHead("");
      return;
    }
    let live = true;
    void file
      .slice(0, HEAD_CHARS * 4)
      .text()
      .then((t) => live && setHead(t.slice(0, HEAD_CHARS)))
      .catch(() => live && setHead(""));
    return () => {
      live = false;
    };
  }, [file, mode]);
  const [pasteHead, setPasteHead] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setPasteHead(pasted.slice(0, HEAD_CHARS)), 400);
    return () => clearTimeout(t);
  }, [pasted]);
  const ask =
    mode === "file"
      ? file
        ? { filename: file.name, mime: file.type || undefined, text_head: head || undefined }
        : null
      : pasteHead.trim() || title.trim()
        ? { filename: title.trim() || undefined, mime: "text/plain", text_head: pasteHead || undefined }
        : null;
  const suggestion = useQuery({
    queryKey: ["suggest-purpose", ask],
    queryFn: () => api.suggestPurpose(ask!),
    enabled: !!ask && !uploaded,
    staleTime: Infinity,
  });
  const suggested = ask ? (suggestion.data?.suggested_purpose ?? null) : null;
  useEffect(() => {
    if (suggested && !picked && initialPurpose === "experience") setPurpose(suggested);
  }, [suggested, picked]);

  const upload = useMutation({
    mutationFn: () =>
      api.uploadSource(mode === "file" ? file! : pastedFile(pasted, title), {
        title: title.trim() || (mode === "paste" ? "붙여넣은 글" : undefined),
        // 증빙은 서버가 늘 제한 등급으로 둔다
        dataClass: purpose === "proof" ? "restricted" : dataClass,
        hiddenText: isDocx && dropHidden ? "exclude" : undefined,
        purpose,
      }),
    onSuccess: (r) => {
      setUploaded(r);
      void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
  const source = useQuery({
    queryKey: ["source", uploaded?.source_id],
    queryFn: () => api.source(uploaded!.source_id),
    enabled: !!uploaded,
    refetchInterval: (q) => {
      const v = q.state.data?.versions.find((x) => x.id === uploaded?.version_id);
      return v?.parse && PARSE_DONE.has(v.parse.status) ? false : 2000;
    },
  });
  const version = source.data?.versions.find((v) => v.id === uploaded?.version_id);
  const parse = version?.parse ?? null;
  // 같은 파일이면 처음 원본의 용도 그대로
  const kept = uploaded?.purpose ?? purpose;

  const extract = useMutation({
    mutationFn: () => api.requestExtraction(parse!.run_id),
    onSuccess: (r) => setExtractionId(r.id),
  });
  const extraction = useQuery({
    queryKey: ["extraction", extractionId],
    queryFn: () => api.extraction(extractionId!),
    enabled: !!extractionId,
    refetchInterval: (q) => (q.state.data && EXTRACT_DONE.has(q.state.data.status) ? false : 2500),
  });
  const extracted = extraction.data && EXTRACT_DONE.has(extraction.data.status);
  useEffect(() => {
    if (extracted) {
      void qc.invalidateQueries({ queryKey: ["experiences"] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
    }
  }, [extracted, qc]);
  const parsed = !!parse && PARSE_DONE.has(parse.status);
  const finished = kept === "experience" ? !!extracted : parsed;
  // 증빙은 읽기가 끝나면 서버가 자격·이수 후보를 뽑는다 (로컬 규칙)
  useEffect(() => {
    if (parsed && kept === "proof") void qc.invalidateQueries({ queryKey: ["credentials"] });
  }, [parsed, kept, qc]);

  const reset = () => {
    setFile(null);
    setPasted("");
    setTitle("");
    setDropHidden(false);
    setPurpose(initialPurpose);
    setPicked(false);
    setUploaded(null);
    setExtractionId(null);
    upload.reset();
    extract.reset();
  };

  const leave = () => {
    onClose();
    reset();
  };

  const uploadErr =
    upload.error && "code" in upload.error && typeof upload.error.code === "string"
      ? UPLOAD_ERROR[upload.error.code]
      : null;
  const ready = mode === "file" ? !!file : pasted.trim().length > 0;

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          onClose();
          if (finished || !uploaded) reset();
        }
      }}
      title="자료 올리기"
      description="원본은 이 서버의 비공개 볼륨에 저장됩니다."
    >
      {!uploaded ? (
        <form
          className="space-y-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) upload.mutate();
          }}
        >
          <div className="space-y-3">
            <div className="-ml-1 flex gap-1" role="radiogroup" aria-label="올리는 방법">
              {(
                [
                  ["file", "파일"],
                  ["paste", "붙여넣기"],
                ] as const
              ).map(([m, label]) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  onClick={() => setMode(m)}
                  className={cn(CHIP, mode === m ? "bg-fg font-medium text-bg" : "text-muted hover:bg-subtle hover:text-fg")}
                >
                  {label}
                </button>
              ))}
            </div>
            {mode === "file" ? (
              <div>
                <Label htmlFor="up-file">파일</Label>
                <Input
                  id="up-file"
                  type="file"
                  accept={ACCEPT}
                  className="h-auto py-1.5"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
                <p className="mt-1 text-[13px] text-muted">PDF, DOCX, PNG, JPEG, 텍스트, Markdown, HTML</p>
              </div>
            ) : (
              <div>
                <Label htmlFor="up-paste">글</Label>
                <Textarea
                  id="up-paste"
                  rows={8}
                  value={pasted}
                  maxLength={MAX_PASTE}
                  onChange={(e) => setPasted(e.target.value)}
                  placeholder="이력서·프로젝트 메모·면접 후기 등을 붙여넣으세요."
                />
              </div>
            )}
            <div>
              <Label htmlFor="up-title">제목 {mode === "file" ? "(선택)" : "(선택, 없으면 “붙여넣은 글”)"}</Label>
              <Input id="up-title" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
          </div>

          <div>
            <PurposeChoice
              name="up-purpose"
              value={purpose}
              suggested={suggested}
              onChange={(v) => {
                setPurpose(v);
                setPicked(true);
              }}
            />
            {suggested && suggestion.data?.matched && !picked ? (
              <p className="mt-1.5 text-[13px] text-muted">
                {suggestion.data.matched === "image" ? "사진 파일이라" : `“${suggestion.data.matched}”가 있어`}{" "}
                {PURPOSE_LABEL[suggested]}로 골랐습니다. 맞는지 확인하세요.
              </p>
            ) : null}
          </div>

          {purpose !== "proof" ? (
            <div>
              <Label htmlFor="up-class">외부 전송 등급</Label>
              <Select id="up-class" value={dataClass} onChange={(e) => setDataClass(e.target.value)}>
                <option value="private">비공개 — 허용한 제공자에만 보냄</option>
                <option value="restricted">제한 — 외부 모델·OCR로 보내지 않음</option>
                <option value="public">공개 자료</option>
              </Select>
            </div>
          ) : null}
          {isDocx ? (
            <label className="flex cursor-pointer items-start gap-2.5 py-1 text-[13px]">
              <input
                type="checkbox"
                className="mt-0.5 size-4 shrink-0 accent-fg"
                checked={dropHidden}
                onChange={(e) => setDropHidden(e.target.checked)}
              />
              <span>
                숨김 서식 글은 빼고 읽기
                <span className="block text-muted">
                  Word에서 숨김으로 표시한 글을 경험 추출·검색에 넣지 않습니다. 숨김 글 검사는 그대로 경고합니다.
                </span>
              </span>
            </label>
          ) : null}
          {uploadErr ? <p className="text-[13px] text-danger">{uploadErr}</p> : <ErrorText error={upload.error} />}
          <Button type="submit" variant="primary" disabled={!ready || upload.isPending}>
            {upload.isPending ? <Spinner /> : null} 올리기
          </Button>
        </form>
      ) : (
        <ol className="space-y-6 text-sm">
          <li className="space-y-1">
            <div className="font-medium">1. 올리기</div>
            <div className="text-[13px] text-muted">
              {version?.original_filename ?? file?.name ?? "붙여넣은 글"} · {PURPOSE_LABEL[kept]}
              {uploaded.deduplicated ? " · 이미 올린 파일과 같아 기존 것을 씁니다" : ""}
              {uploaded.hidden_text === "exclude" ? " · 숨김 서식 글은 빼고 읽습니다" : ""}
              {uploaded.deduplicated && dropHidden && uploaded.hidden_text === "keep"
                ? " (처음 올릴 때 숨김 글을 넣고 읽어 그대로 씁니다)"
                : ""}
            </div>
            {uploaded.deduplicated && uploaded.purpose !== purpose ? (
              <p className="text-[13px]">
                용도는 처음 올릴 때의 {PURPOSE_LABEL[uploaded.purpose]} 그대로입니다.{" "}
                <SourceLink id={uploaded.source_id} onGo={leave}>
                  자료 화면에서 수정
                </SourceLink>
              </p>
            ) : null}
          </li>
          <li className="space-y-1">
            <div className="font-medium">2. 읽기</div>
            {parse ? (
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className={STATE_TEXT[stateOf(parse.status)]}>{PARSE_LABEL[parse.status] ?? parse.status}</span>
                {parse.pages_total ? <span className="text-muted tabular-nums">{parse.pages_total}쪽</span> : null}
                {parse.missing_pages.length ? (
                  <span className="font-medium tabular-nums">못 읽은 쪽: {parse.missing_pages.join(", ")}</span>
                ) : null}
                {parse.error_code ? <span className="text-danger">{parse.error_code}</span> : null}
                {!PARSE_DONE.has(parse.status) ? <Spinner /> : null}
              </div>
            ) : (
              <div className="flex items-center gap-2 text-[13px] text-muted">
                <Spinner /> 작업 대기 (worker가 켜져 있어야 합니다)
              </div>
            )}
          </li>
          {kept === "experience" ? (
            <li className="space-y-2">
              <div className="font-medium">3. 경험 추출</div>
              {!extractionId ? (
                <>
                  <p className="text-[13px] text-muted">
                    읽은 내용을 모델에 보내 경험·주장 후보를 뽑습니다. 모델 비용이 들고, 결과는 모두 “확인 필요” 상태로
                    들어옵니다.
                  </p>
                  <Button
                    variant="primary"
                    disabled={!parse || !["complete", "partial"].includes(parse.status) || extract.isPending}
                    onClick={() => extract.mutate()}
                  >
                    추출 요청
                  </Button>
                  <ErrorText error={extract.error} />
                </>
              ) : extraction.data ? (
                <div className="space-y-1 text-[13px]">
                  <div className="flex items-center gap-2">
                    <span className={STATE_TEXT[stateOf(extraction.data.status)]}>
                      {{ pending: "추출 중", complete: "완료", partial: "일부만 보냄", failed: "실패" }[
                        extraction.data.status
                      ] ?? extraction.data.status}
                    </span>
                    {!extracted ? <Spinner /> : null}
                    {extraction.data.error_code ? (
                      <span className="text-danger">{extraction.data.error_code}</span>
                    ) : null}
                  </div>
                  {extracted ? (
                    <p className="text-muted tabular-nums">
                      경험 {extraction.data.experiences_created}개 · 주장 {extraction.data.claims_created}개
                      {extraction.data.claims_dropped
                        ? ` · 원문에서 근거를 못 찾아 버린 주장 ${extraction.data.claims_dropped}개`
                        : ""}
                      {extraction.data.status === "partial"
                        ? ` · 블록 ${extraction.data.blocks_sent}/${extraction.data.blocks_total}개만 보냄`
                        : ""}
                    </p>
                  ) : null}
                  {extracted ? (
                    <Link
                      to="/brain"
                      search={{ review: true }}
                      onClick={() => {
                        onClose();
                        reset();
                      }}
                      className="inline-flex min-h-9 items-center font-medium underline underline-offset-2"
                    >
                      확인하러 가기
                    </Link>
                  ) : null}
                </div>
              ) : (
                <Spinner />
              )}
            </li>
          ) : (
            <li className="space-y-1">
              <div className="font-medium">3. 보관</div>
              <p className="text-[13px] text-muted">
                {kept === "proof"
                  ? "증빙 서류로 보관합니다. 경험을 뽑지 않고, 외부 모델에 보내지 않으며 검색에 나오지 않습니다."
                  : "참고 자료로 보관합니다. 경험을 뽑지 않고, 검색에서 “참고 자료 포함”을 켤 때만 나옵니다."}
              </p>
              <SourceLink id={uploaded.source_id} onGo={leave}>
                자료 보기
              </SourceLink>
            </li>
          )}
          <li className="border-t border-line pt-3">
            <Button variant="ghost" size="sm" className="-ml-2.5 max-sm:h-9" onClick={reset}>
              다른 자료 올리기
            </Button>
          </li>
        </ol>
      )}
    </Sheet>
  );
}

function SourceLink({ id, onGo, children }: { id: string; onGo: () => void; children: string }) {
  return (
    <Link
      to="/brain/sources/$sourceId"
      params={{ sourceId: id }}
      onClick={onGo}
      className="inline-flex min-h-9 items-center text-[13px] font-medium underline underline-offset-2"
    >
      {children}
    </Link>
  );
}
