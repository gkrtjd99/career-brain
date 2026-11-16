import type { UploadOut } from "@career-brain/client";
import { Badge, Button, Input, Label, Select, Sheet, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ErrorText } from "./common";

const ACCEPT =
  ".pdf,.docx,.png,.jpg,.jpeg,.txt,.md,.html,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,text/plain,text/markdown,text/html";
const PARSE_DONE = new Set(["complete", "partial", "failed"]);
const EXTRACT_DONE = new Set(["complete", "partial", "failed"]);

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

/** 이력서·포트폴리오 올리기 → 읽기(파싱) → 경험 추출 요청. 진행 상태를 그대로 보여준다. */
export function UploadSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [dataClass, setDataClass] = useState("private");
  // DOCX 숨김 서식 글(w:vanish)을 원본 블록에서 뺄지 (기본은 넣고 검사가 경고)
  const [dropHidden, setDropHidden] = useState(false);
  const isDocx = !!file && /\.docx$/i.test(file.name);
  const [uploaded, setUploaded] = useState<UploadOut | null>(null);
  const [extractionId, setExtractionId] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: () =>
      api.uploadSource(file!, {
        title: title.trim() || undefined,
        dataClass,
        hiddenText: isDocx && dropHidden ? "exclude" : undefined,
      }),
    onSuccess: setUploaded,
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
    if (extracted) void qc.invalidateQueries({ queryKey: ["experiences"] });
  }, [extracted, qc]);

  const reset = () => {
    setFile(null);
    setTitle("");
    setDropHidden(false);
    setUploaded(null);
    setExtractionId(null);
    upload.reset();
    extract.reset();
  };

  const uploadErr =
    upload.error && "code" in upload.error && typeof upload.error.code === "string"
      ? UPLOAD_ERROR[upload.error.code]
      : null;

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          onClose();
          if (extracted || !uploaded) reset();
        }
      }}
      title="자료 올리기"
      description="원본은 이 서버의 비공개 볼륨에 저장됩니다."
    >
      {!uploaded ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (file) upload.mutate();
          }}
        >
          <div>
            <Label htmlFor="up-file">파일</Label>
            <Input
              id="up-file"
              type="file"
              accept={ACCEPT}
              className="h-auto py-1.5"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <p className="mt-1 text-xs text-muted">PDF, DOCX, PNG, JPEG, 텍스트, Markdown, HTML</p>
          </div>
          <div>
            <Label htmlFor="up-title">제목 (선택)</Label>
            <Input id="up-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="up-class">외부 전송 등급</Label>
            <Select id="up-class" value={dataClass} onChange={(e) => setDataClass(e.target.value)}>
              <option value="private">비공개 — 허용한 제공자에만 보냄</option>
              <option value="restricted">제한 — 외부 모델·OCR로 보내지 않음</option>
              <option value="public">공개 자료</option>
            </Select>
          </div>
          {isDocx ? (
            <label className="flex items-start gap-2 text-[13px]">
              <input
                type="checkbox"
                className="mt-1"
                checked={dropHidden}
                onChange={(e) => setDropHidden(e.target.checked)}
              />
              <span>
                숨김 서식 글은 빼고 읽기
                <span className="block text-xs text-muted">
                  Word에서 숨김으로 표시한 글을 경험 추출·검색에 넣지 않습니다. 숨김 글 검사는 그대로 경고합니다.
                </span>
              </span>
            </label>
          ) : null}
          {uploadErr ? <p className="text-[13px] text-danger">{uploadErr}</p> : <ErrorText error={upload.error} />}
          <Button type="submit" variant="primary" disabled={!file || upload.isPending}>
            {upload.isPending ? <Spinner /> : null} 올리기
          </Button>
        </form>
      ) : (
        <ol className="space-y-4 text-sm">
          <li className="space-y-1">
            <div className="font-medium">1. 올리기</div>
            <div className="text-[13px] text-muted">
              {version?.original_filename ?? file?.name}
              {uploaded.deduplicated ? " · 이미 올린 파일과 같아 기존 것을 씁니다" : ""}
              {uploaded.hidden_text === "exclude" ? " · 숨김 서식 글은 빼고 읽습니다" : ""}
              {uploaded.deduplicated && dropHidden && uploaded.hidden_text === "keep"
                ? " (처음 올릴 때 숨김 글을 넣고 읽어 그대로 씁니다)"
                : ""}
            </div>
          </li>
          <li className="space-y-1">
            <div className="font-medium">2. 읽기</div>
            {parse ? (
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <Badge
                  tone={
                    parse.status === "complete"
                      ? "ok"
                      : parse.status === "failed"
                        ? "danger"
                        : parse.status === "partial"
                          ? "warn"
                          : "accent"
                  }
                >
                  {PARSE_LABEL[parse.status] ?? parse.status}
                </Badge>
                {parse.pages_total ? <span className="text-muted">{parse.pages_total}쪽</span> : null}
                {parse.missing_pages.length ? (
                  <span className="text-warn">못 읽은 쪽: {parse.missing_pages.join(", ")}</span>
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
          <li className="space-y-2">
            <div className="font-medium">3. 경험 추출</div>
            {!extractionId ? (
              <>
                <p className="text-[13px] text-muted">
                  읽은 내용을 모델에 보내 경험·주장 후보를 뽑습니다. 모델 비용이 들고, 결과는 모두
                  “확인 필요” 상태로 들어옵니다.
                </p>
                <Button
                  variant="primary"
                  disabled={
                    !parse || !["complete", "partial"].includes(parse.status) || extract.isPending
                  }
                  onClick={() => extract.mutate()}
                >
                  추출 요청
                </Button>
                <ErrorText error={extract.error} />
              </>
            ) : extraction.data ? (
              <div className="space-y-1 text-[13px]">
                <div className="flex items-center gap-2">
                  <Badge
                    tone={
                      extraction.data.status === "complete"
                        ? "ok"
                        : extraction.data.status === "failed"
                          ? "danger"
                          : extraction.data.status === "partial"
                            ? "warn"
                            : "accent"
                    }
                  >
                    {{ pending: "추출 중", complete: "완료", partial: "일부만 보냄", failed: "실패" }[
                      extraction.data.status
                    ] ?? extraction.data.status}
                  </Badge>
                  {!extracted ? <Spinner /> : null}
                  {extraction.data.error_code ? (
                    <span className="text-danger">{extraction.data.error_code}</span>
                  ) : null}
                </div>
                {extracted ? (
                  <p className="text-muted">
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
                    className="inline-block pt-1 text-accent underline-offset-2 hover:underline"
                  >
                    확인하러 가기
                  </Link>
                ) : null}
              </div>
            ) : (
              <Spinner />
            )}
          </li>
          <li>
            <Button variant="ghost" size="sm" onClick={reset}>
              다른 파일 올리기
            </Button>
          </li>
        </ol>
      )}
    </Sheet>
  );
}
