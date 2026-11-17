import type { SaveUrlResult } from "@career-brain/client";
import { Button, cn, Spinner } from "@career-brain/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus, X } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { ERROR_TEXT, FETCH_ERROR } from "../../lib/labels";
import { ErrorText } from "../common";

export const MAX_URLS = 20;

/** 붙여넣은 글에서 URL만 뽑는다 (줄바꿈·공백·쉼표 구분, 중복 제거). */
export function extractUrls(text: string): string[] {
  const found = text.match(/https?:\/\/[^\s<>"'，,]+/gi) ?? [];
  return [...new Set(found.map((u) => u.replace(/[).,;]+$/, "")))];
}

/**
 * 공고 추가: 평소엔 한 줄. 여러 URL을 붙여넣으면 줄마다 하나씩 보이게 늘어난다.
 * Enter로 추가, Shift+Enter로 줄바꿈.
 */
export function AddPostings() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [results, setResults] = useState<SaveUrlResult[] | null>(null);
  const urls = extractUrls(text);
  const lines = Math.min(6, Math.max(1, text.split("\n").length));
  const save = useMutation({
    mutationFn: () => api.savePostings(urls.slice(0, MAX_URLS)),
    onSuccess: (r) => {
      setResults(r);
      setText("");
      qc.invalidateQueries({ queryKey: ["postings"] });
    },
  });
  const submit = () => {
    if (urls.length && !save.isPending) save.mutate();
  };

  return (
    <section aria-label="공고 추가">
      <form
        className="flex items-start gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="posting-urls" className="sr-only">
          공고 URL
        </label>
        <textarea
          id="posting-urls"
          rows={lines}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            // 여러 URL을 붙여넣으면 줄마다 하나로 펴서 보인다
            const pasted = e.clipboardData.getData("text");
            const found = extractUrls(pasted);
            if (found.length > 1) {
              e.preventDefault();
              setText((t) => [t.trim(), ...found].filter(Boolean).join("\n"));
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="공고 URL 붙여넣기 (여러 개도 됩니다)"
          spellCheck={false}
          className={cn(
            "min-h-9 min-w-0 flex-1 resize-none rounded-md border border-line bg-surface px-3 py-[7px] text-sm leading-5",
            "placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-accent",
            lines > 1 && "font-mono text-[13px]",
          )}
        />
        <Button type="submit" variant="primary" disabled={!urls.length || save.isPending}>
          {save.isPending ? <Spinner /> : <Plus size={14} />}
          {urls.length > 1 ? `${Math.min(urls.length, MAX_URLS)}건 추가` : "공고 추가"}
        </Button>
      </form>
      {urls.length > MAX_URLS ? (
        <p className="mt-1.5 text-[13px] font-medium">
          한 번에 {MAX_URLS}건까지 보냅니다. 앞의 {MAX_URLS}건만 추가합니다.
        </p>
      ) : urls.length > 1 ? (
        <p className="mt-1.5 text-[13px] text-muted">같은 공고는 한 건으로 합칩니다.</p>
      ) : null}
      <ErrorText error={save.error} className="mt-1.5" />
      {results ? <SaveResults results={results} onDismiss={() => setResults(null)} /> : null}
    </section>
  );
}

/** 방금 보낸 URL의 결과. 새로 가져오는 공고는 아래 "확인할 공고"에도 뜬다. */
function SaveResults({ results, onDismiss }: { results: SaveUrlResult[]; onDismiss: () => void }) {
  const fresh = results.filter((r) => r.posting_id && !r.deduplicated && !r.error).length;
  const dup = results.filter((r) => r.deduplicated).length;
  const bad = results.filter((r) => r.error);
  return (
    <div className="mt-2 text-[13px]">
      <div className="flex min-h-9 items-center gap-2">
        <p role="status" className="min-w-0 flex-1 text-muted">
          {[
            fresh ? `${fresh}건 가져오는 중입니다` : null,
            dup ? `이미 있는 공고 ${dup}건` : null,
            bad.length ? `추가하지 못한 URL ${bad.length}건` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
          {results.length === 1 && results[0]!.posting_id ? (
            <>
              {" · "}
              <Link
                to="/postings/$postingId"
                params={{ postingId: results[0]!.posting_id }}
                className="text-fg underline underline-offset-2"
              >
                열기
              </Link>
            </>
          ) : null}
        </p>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="결과 닫기"
          className="-mr-2 inline-flex size-9 items-center justify-center rounded-md text-muted hover:text-fg"
        >
          <X size={14} />
        </button>
      </div>
      {bad.length ? (
        <ul className="divide-y divide-line border-y border-line">
          {bad.map((r, i) => (
            <li key={`${r.url}-${i}`} className="flex min-h-9 items-center gap-3 py-1.5">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">{r.url}</span>
              <span className="shrink-0 text-danger">{FETCH_ERROR[r.error!] ?? ERROR_TEXT[r.error!] ?? r.error}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
