import type { PostingStatus, PostingSummary, SaveUrlResult } from "@career-brain/client";
import { Badge, Button, EmptyState, Section, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { ErrorText, List, Loaded, PageHeader } from "../components/common";
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
    <div>
      <PageHeader
        title="공고"
        subtitle="붙여넣은 URL의 페이지 한 건씩만 가져옵니다. 확인해서 저장한 공고만 지원 준비에 쓰입니다."
      />

      <Section title="URL 붙여넣기" className="mb-8">
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (urls.length) save.mutate();
          }}
        >
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"https://example.com/jobs/123\nhttps://example.com/jobs/456"}
            aria-label="공고 URL"
            className="min-h-24 font-mono text-[13px]"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary" disabled={!urls.length || save.isPending}>
              {save.isPending ? <Spinner /> : null}
              {urls.length ? `${Math.min(urls.length, MAX_URLS)}건 가져오기` : "가져오기"}
            </Button>
            <span className="text-xs text-muted">
              {urls.length > MAX_URLS
                ? `한 번에 ${MAX_URLS}건까지. 앞의 ${MAX_URLS}건만 보냅니다.`
                : "여러 URL은 줄을 바꿔 붙여넣으세요. 같은 공고는 한 건으로 합칩니다."}
            </span>
          </div>
          <ErrorText error={save.error} />
        </form>
        {results ? <SaveResults results={results} onDismiss={() => setResults(null)} /> : null}
      </Section>

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
            <Section
              title="가져온 공고"
              action={list.isFetching ? <Spinner /> : null}
            >
              <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="상태">
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
                    role="radio"
                    aria-checked={filter === v}
                    onClick={() => setFilter(v)}
                    className={
                      filter === v
                        ? "rounded-full bg-fg px-3 py-1 text-[13px] font-medium text-bg"
                        : "rounded-full border border-line bg-surface px-3 py-1 text-[13px] text-muted hover:text-fg"
                    }
                  >
                    {label} {counts[v]}
                  </button>
                ))}
              </div>
              {shown.length ? (
                <List>
                  {shown.map((p) => (
                    <PostingRow key={p.id} p={p} />
                  ))}
                </List>
              ) : (
                <EmptyState>공고가 없습니다.</EmptyState>
              )}
            </Section>
          );
        }}
      </Loaded>
    </div>
  );
}

function PostingRow({ p }: { p: PostingSummary }) {
  const fetch = p.last_fetch_status ? FETCH_STATUS[p.last_fetch_status] : null;
  const st = POSTING_STATUS[p.status];
  return (
    <li>
      <Link
        to="/postings/$postingId"
        params={{ postingId: p.id }}
        className="flex items-center gap-3 px-3 py-3 hover:bg-subtle/60"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{p.title ?? <span className="text-muted">제목 없음</span>}</div>
          <div className="truncate text-xs text-muted">
            {[
              p.company,
              p.deadline ? `마감 ${formatDeadline(p.deadline)}` : null,
              hostOf(p.canonical_url),
              p.saved_at ? `저장 ${formatDate(p.saved_at)}` : p.created_at ? `추가 ${formatDate(p.created_at)}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
          {p.last_fetch_status === "failed" ? (
            <div className="mt-0.5 text-xs text-danger">
              {FETCH_ERROR[p.last_fetch_error ?? ""] ?? p.last_fetch_error} · 마감으로 처리하지 않음
            </div>
          ) : null}
          {p.pending_changes.length ? (
            <div className="mt-0.5 text-xs text-warn">
              변경 확인 필요: {p.pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-center">
          <DdayBadge deadline={p.deadline} />
          {fetch && p.last_fetch_status !== "done" ? (
            <Badge tone={fetch.tone}>
              {p.last_fetch_status === "pending" ? <Spinner className="h-3 w-3" /> : null}
              {fetch.label}
            </Badge>
          ) : null}
          <Badge tone={st.tone}>{st.label}</Badge>
        </div>
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function SaveResults({ results, onDismiss }: { results: SaveUrlResult[]; onDismiss: () => void }) {
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-3 py-2 text-[13px]">
        <span className="font-medium">요청 결과</span>
        <button onClick={onDismiss} className="text-muted hover:text-fg">
          닫기
        </button>
      </div>
      <ul className="divide-y divide-line">
        {results.map((r, i) => (
          <li key={`${r.url}-${i}`} className="flex items-center gap-2 px-3 py-2 text-[13px]">
            <span className="min-w-0 flex-1 truncate font-mono text-xs">{r.url}</span>
            {r.error ? (
              <Badge tone="danger">{FETCH_ERROR[r.error] ?? ERROR_TEXT[r.error] ?? r.error}</Badge>
            ) : r.deduplicated ? (
              <Badge tone="none">이미 있는 공고</Badge>
            ) : (
              <Badge tone="accent">가져오는 중</Badge>
            )}
            {r.posting_id ? (
              <Link
                to="/postings/$postingId"
                params={{ postingId: r.posting_id }}
                className="shrink-0 text-accent underline-offset-2 hover:underline"
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
