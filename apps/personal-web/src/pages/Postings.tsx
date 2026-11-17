import type { PostingStatus, PostingSummary, SaveUrlResult } from "@career-brain/client";
import { Badge, Button, cn, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { DdayBadge } from "../components/DdayBadge";
import { api } from "../lib/api";
import { formatDate, formatDeadline } from "../lib/dates";
import { hostOf } from "../lib/format";
import { ERROR_TEXT, FETCH_ERROR, FETCH_STATUS, FIELD_LABEL, POSTING_STATUS } from "../lib/labels";

const MAX_URLS = 20;

/** 붙여넣은 글에서 URL만 뽑는다 (줄바꿈·공백·쉼표 구분, 중복 제거). */
export function extractUrls(text: string): string[] {
  const found = text.match(/https?:\/\/[^\s<>"'，,]+/gi) ?? [];
  return [...new Set(found.map((u) => u.replace(/[).,;]+$/, "")))];
}

type Filter = "active" | PostingStatus;

export function PostingsPage() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [results, setResults] = useState<SaveUrlResult[] | null>(null);
  const [filter, setFilter] = useState<Filter>("active");
  const urls = extractUrls(text);

  const list = useQuery({
    queryKey: ["postings"],
    queryFn: api.postings,
    // 가져오는 중인 공고가 있으면 상태를 따라간다.
    refetchInterval: (q) => (q.state.data?.some((p) => p.last_fetch_status === "pending") ? 2500 : false),
  });
  const save = useMutation({
    mutationFn: () => api.savePostings(urls.slice(0, MAX_URLS)),
    onSuccess: (r) => {
      setResults(r);
      setText("");
      qc.invalidateQueries({ queryKey: ["postings"] });
    },
  });

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="공고"
        subtitle="URL마다 그 페이지 한 건만 가져옵니다. 확인해서 저장한 공고만 지원 준비에 쓰입니다."
      />

      <div className="space-y-10">
        <section>
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (urls.length) save.mutate();
            }}
          >
            <label htmlFor="posting-urls" className="mb-2 block text-[15px] font-semibold">
              URL 붙여넣기
            </label>
            <Textarea
              id="posting-urls"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={"https://example.com/jobs/123\nhttps://example.com/jobs/456"}
              className="min-h-24 font-mono text-[13px]"
            />
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <Button type="submit" variant="primary" disabled={!urls.length || save.isPending}>
                {save.isPending ? <Spinner /> : null}
                {urls.length ? `${Math.min(urls.length, MAX_URLS)}건 가져오기` : "가져오기"}
              </Button>
              <span className="text-[13px] text-muted">
                {urls.length > MAX_URLS
                  ? `한 번에 ${MAX_URLS}건까지. 앞의 ${MAX_URLS}건만 보냅니다.`
                  : "한 줄에 하나씩. 같은 공고는 한 건으로 합칩니다."}
              </span>
            </div>
            <ErrorText error={save.error} />
          </form>
          {results ? <SaveResults results={results} onDismiss={() => setResults(null)} /> : null}
        </section>

        <Loaded query={list}>
          {(items) => {
            const counts = {
              active: items.filter((p) => p.status !== "archived").length,
              draft: items.filter((p) => p.status === "draft").length,
              saved: items.filter((p) => p.status === "saved").length,
              archived: items.filter((p) => p.status === "archived").length,
            };
            const shown = items.filter((p) => (filter === "active" ? p.status !== "archived" : p.status === filter));
            return (
              <section>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h2 className="text-[15px] font-semibold">가져온 공고</h2>
                  {list.isFetching ? <Spinner /> : null}
                </div>
                <div className="mb-2 flex flex-wrap gap-1" role="radiogroup" aria-label="상태">
                  {(
                    [
                      ["active", "전체"],
                      ["draft", "확인 필요"],
                      ["saved", "저장됨"],
                      ["archived", "보관"],
                    ] as [Filter, string][]
                  ).map(([v, label]) => (
                    <button
                      key={v}
                      type="button"
                      role="radio"
                      aria-checked={filter === v}
                      onClick={() => setFilter(v)}
                      className={cn(
                        "inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-[13px] transition-colors duration-150",
                        filter === v ? "bg-subtle font-medium text-fg" : "text-muted hover:text-fg",
                      )}
                    >
                      {label}
                      <span className="tabular-nums text-muted">{counts[v]}</span>
                    </button>
                  ))}
                </div>
                {shown.length ? (
                  <ul className="divide-y divide-line border-t border-line">
                    {shown.map((p) => (
                      <PostingRow key={p.id} p={p} />
                    ))}
                  </ul>
                ) : (
                  <p className="border-t border-line pt-3 text-sm text-muted">
                    {filter === "active" ? (
                      "아직 가져온 공고가 없습니다. 위에 공고 URL을 붙여넣으면 여기에 쌓입니다."
                    ) : (
                      <>
                        이 상태의 공고가 없습니다.{" "}
                        <button
                          type="button"
                          onClick={() => setFilter("active")}
                          className="underline underline-offset-2 hover:text-fg"
                        >
                          전체 보기
                        </button>
                      </>
                    )}
                  </p>
                )}
              </section>
            );
          }}
        </Loaded>
      </div>
    </div>
  );
}

function PostingRow({ p }: { p: PostingSummary }) {
  const fetch = p.last_fetch_status ? FETCH_STATUS[p.last_fetch_status] : null;
  // 배지는 손대야 하는 상태 하나만: 목록 페이지·비용 확인 > 확인 필요. 가져오는 중은 스피너, 실패는 아래 빨간 줄.
  const needsFetchAction = p.last_fetch_status === "listing" || p.last_fetch_status === "needs_confirmation";
  return (
    <li>
      <Link
        to="/postings/$postingId"
        params={{ postingId: p.id }}
        className="-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{p.title ?? <span className="text-muted">제목 없음</span>}</div>
          <div className="truncate text-[13px] text-muted tabular-nums">
            {[
              p.company,
              hostOf(p.canonical_url),
              p.deadline ? `마감 ${formatDeadline(p.deadline)}` : null,
              p.saved_at ? `저장 ${formatDate(p.saved_at)}` : p.created_at ? `추가 ${formatDate(p.created_at)}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
          {p.last_fetch_status === "failed" ? (
            <div className="mt-0.5 text-[13px] text-danger">
              가져오기 실패 · {FETCH_ERROR[p.last_fetch_error ?? ""] ?? p.last_fetch_error} · 마감으로 처리하지 않음
            </div>
          ) : null}
          {p.pending_changes.length ? (
            <div className="mt-0.5 text-[13px]">
              변경 확인 필요: {p.pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 text-right">
          {p.last_fetch_status === "pending" ? (
            <span className="inline-flex items-center gap-1.5 text-[13px] text-muted">
              <Spinner className="h-3 w-3" /> {fetch?.label}
            </span>
          ) : needsFetchAction && fetch ? (
            <Badge tone={fetch.tone}>{fetch.label}</Badge>
          ) : p.status === "draft" ? (
            <Badge tone="warn">{POSTING_STATUS.draft.label}</Badge>
          ) : p.status === "archived" ? (
            <span className="text-[13px] text-muted">{POSTING_STATUS.archived.label}</span>
          ) : null}
          <DdayBadge deadline={p.deadline} />
        </div>
      </Link>
    </li>
  );
}

function SaveResults({ results, onDismiss }: { results: SaveUrlResult[]; onDismiss: () => void }) {
  return (
    <div className="mt-6">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">요청 결과</h3>
        <button
          type="button"
          onClick={onDismiss}
          className="-mr-2 h-9 rounded-md px-2 text-[13px] text-muted hover:text-fg"
        >
          닫기
        </button>
      </div>
      <ul className="divide-y divide-line border-t border-line">
        {results.map((r, i) => (
          <li key={`${r.url}-${i}`} className="flex min-h-11 items-center gap-3 py-2 text-[13px]">
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">{r.url}</span>
            {r.error ? (
              <span className="shrink-0 text-danger">{FETCH_ERROR[r.error] ?? ERROR_TEXT[r.error] ?? r.error}</span>
            ) : r.deduplicated ? (
              <span className="shrink-0 text-muted">이미 있는 공고</span>
            ) : (
              <span className="shrink-0">가져오는 중</span>
            )}
            {r.posting_id ? (
              <Link
                to="/postings/$postingId"
                params={{ postingId: r.posting_id }}
                className="shrink-0 font-medium underline-offset-2 hover:underline"
              >
                열기
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
