import type { SearchHit, SearchKind } from "@career-brain/client";
import { Badge, Button, EmptyState, Input, StatusBadge } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { FileText, MessageSquareQuote, Quote, Search as SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Loaded, PageHeader } from "../components/common";
import { SourceSheet, type SourceTarget } from "../components/SourceSheet";
import { api } from "../lib/api";

const route = getRouteApi("/search");

const KIND_INFO: Record<SearchKind, { label: string; icon: typeof Quote }> = {
  claim: { label: "주장", icon: Quote },
  block: { label: "원문", icon: FileText },
  answer: { label: "인터뷰 답변", icon: MessageSquareQuote },
};
const ALL: SearchKind[] = ["claim", "block", "answer"];

function semanticNote(note: string) {
  if (note === "no_embedding_route") return "임베딩 모델 미설정";
  if (note === "egress_scope_required") return "외부 전송 권한 없음";
  if (note === "index_empty") return "색인이 비어 있음";
  if (note === "empty_query") return "빈 검색어";
  return note;
}

export function SearchPage() {
  const { q = "", kinds: kindsParam } = route.useSearch();
  const navigate = useNavigate();
  const kinds = (kindsParam?.split(",") as SearchKind[] | undefined) ?? ALL;
  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);

  const result = useQuery({
    queryKey: ["search", q, kinds.join(",")],
    queryFn: ({ signal }) => api.search(q, { kinds, limit: 30 }, signal),
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });

  const go = (nextQ: string, nextKinds: SearchKind[]) =>
    navigate({
      to: "/search",
      search: {
        ...(nextQ.trim() ? { q: nextQ.trim() } : {}),
        ...(nextKinds.length && nextKinds.length < ALL.length ? { kinds: nextKinds.join(",") } : {}),
      },
      replace: true,
    });

  const toggle = (k: SearchKind) => {
    const next = kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k];
    go(q, next.length ? next : ALL);
  };

  return (
    <div>
      <PageHeader title="검색" subtitle="확인한 주장, 올린 원문, 인터뷰 답변에서 찾습니다." />
      <form
        role="search"
        className="mb-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          go(draft, kinds);
        }}
      >
        <Input
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="예: 배송 지연 개선, Kafka, 응답 시간"
          aria-label="검색어"
          maxLength={500}
          autoFocus
        />
        <Button type="submit" variant="primary" aria-label="검색">
          <SearchIcon size={16} />
          <span className="hidden sm:inline">검색</span>
        </Button>
      </form>
      <div className="mb-5 flex flex-wrap gap-1.5">
        {ALL.map((k) => (
          <button
            key={k}
            aria-pressed={kinds.includes(k)}
            onClick={() => toggle(k)}
            className={
              kinds.includes(k)
                ? "rounded-full bg-accent-soft px-3 py-1 text-[13px] font-medium text-accent"
                : "rounded-full border border-line bg-surface px-3 py-1 text-[13px] text-muted"
            }
          >
            {KIND_INFO[k].label}
          </button>
        ))}
      </div>

      {!q ? (
        <EmptyState>검색어를 넣어 주세요. 키워드와 의미 검색을 함께 씁니다.</EmptyState>
      ) : (
        <Loaded query={result}>
          {(r) => (
            <div className={result.isFetching ? "opacity-60" : undefined}>
              <p className="mb-2 text-xs text-muted">
                {r.hits.length}건 · {r.mode === "hybrid" ? "키워드 + 의미" : r.mode === "keyword" ? "키워드" : r.mode}
                {r.semantic_note ? ` · 의미 검색 안 함: ${semanticNote(r.semantic_note)}` : ""}
              </p>
              {r.hits.length ? <Hits hits={r.hits} /> : <EmptyState>찾은 결과가 없습니다.</EmptyState>}
            </div>
          )}
        </Loaded>
      )}
    </div>
  );
}

function Hits({ hits }: { hits: SearchHit[] }) {
  const [source, setSource] = useState<SourceTarget | null>(null);
  return (
    <>
    <ul className="space-y-2">
        {hits.map((h) => {
          const info = KIND_INFO[h.kind] ?? KIND_INFO.claim;
          const Icon = info.icon;
          return (
            <li key={`${h.kind}-${h.id}`} className="rounded-lg border border-line bg-surface p-3">
              <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                <Icon size={14} />
                <span>{info.label}</span>
                {h.claim_status ? <StatusBadge status={h.claim_status} /> : null}
                {h.kind === "block" ? <Badge tone="none">원문 그대로</Badge> : null}
              </div>
              {h.question ? <p className="text-xs text-muted">질문: {h.question}</p> : null}
              <p className="text-sm leading-6 whitespace-pre-wrap">{h.snippet}</p>
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                {h.experience_id ? (
                  <Link
                    to="/brain/$experienceId"
                    params={{ experienceId: h.experience_id }}
                    className="text-accent underline-offset-2 hover:underline"
                  >
                    {h.experience_title ?? "경험"} 열기
                  </Link>
                ) : null}
                {h.kind === "block" && h.parse_run_id && h.block_id ? (
                  <button
                    className="text-accent underline-offset-2 hover:underline"
                    onClick={() =>
                      setSource({
                        parseRunId: h.parse_run_id!,
                        blockId: h.block_id!,
                        sourceTitle: h.source_title,
                        page: h.page,
                        matchStart: h.match_start,
                        matchEnd: h.match_end,
                      })
                    }
                  >
                    원본 보기
                  </button>
                ) : null}
                {h.source_title ? (
                  <span className="text-muted">
                    {h.source_title}
                    {h.page ? ` · ${h.page}쪽` : ""}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
    </ul>
    <SourceSheet target={source} onClose={() => setSource(null)} />
    </>
  );
}
