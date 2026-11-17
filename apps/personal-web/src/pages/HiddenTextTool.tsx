import type { FileCheck } from "@career-brain/client";
import { Button } from "@career-brain/ui";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorText, PageHeader } from "../components/common";
import { FindingList } from "../components/doc/ExportSheet";
import { api } from "../lib/api";

/**
 * 도구 › 숨김 글 검사: 다른 도구로 만든 제출 파일(DOCX·PDF)을 서버가 읽어 숨김 서식·흰 글씨·아주 작은 글자·
 * 쪽 밖 글·심사 프로그램에 지시하는 문장을 찾는다. 파일은 저장하지 않고 바꾸지 않는다.
 * (공용 문서 화면 아래의 검사와 같은 API: POST /core/file-checks/hidden-text)
 */
export function HiddenTextToolPage() {
  const [file, setFile] = useState<File | null>(null);
  const run = useMutation({ mutationFn: (f: File) => api.checkHiddenText(f) });
  const res: FileCheck | undefined = run.data;
  const head = res
    ? `${res.filename ?? "파일"} · ${res.format.toUpperCase()}${res.pages ? ` · ${res.pages}쪽` : ""}`
    : "";
  return (
    <div className="max-w-3xl">
      <PageHeader
        title="숨김 글 검사"
        subtitle="파일은 저장하지 않습니다."
      />
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (file) run.mutate(file);
        }}
      >
        <input
          type="file"
          aria-label="검사할 DOCX·PDF 파일"
          accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="max-w-full min-w-0 text-[13px] text-muted file:mr-3 file:h-9 file:rounded-md file:border file:border-line-strong file:bg-surface file:px-3 file:text-[13px] file:font-medium file:text-fg hover:file:bg-subtle"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            run.reset();
          }}
        />
        <Button type="submit" variant="primary" disabled={!file || run.isPending}>
          {run.isPending ? "검사하는 중" : "검사"}
        </Button>
      </form>
      <ErrorText error={run.error} className="mt-3" />
      <div className="mt-6">
        {res ? (
          res.findings.length ? (
            <div className="space-y-2">
              <p className="text-[13px] font-medium tabular-nums">
                {head} · 경고 {res.findings.length}개
              </p>
              <FindingList findings={res.findings} />
            </div>
          ) : (
            <p className="border-t border-line pt-3 text-sm text-muted tabular-nums">{head} · 숨김 글 경고가 없습니다.</p>
          )
        ) : (
          <p className="border-t border-line pt-3 text-sm text-muted">
            검사할 파일을 고르세요. 이 앱에서 만든 문서는 내보낼 때 같은 검사를 거칩니다.
          </p>
        )}
      </div>
    </div>
  );
}
