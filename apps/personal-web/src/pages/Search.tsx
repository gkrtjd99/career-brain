import type { SearchHit, SearchKind } from "@career-brain/client";
import { Button, cn, Input, STATUS_LABEL, type VerificationStatus } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { BookOpen, FileText, MessageSquareQuote, NotebookPen, Quote, Search as SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Loaded, PageHeader } from "../components/common";
import { SourceSheet, type SourceTarget } from "../components/SourceSheet";
import { api } from "../lib/api";

const route = getRouteApi("/search");

const KIND_INFO: Record<SearchKind, { label: string; icon: typeof Quote }> = {
  claim: { label: "주장", icon: Quote },
  block: { label: "원문", icon: FileText },
  answer: { label: "인터뷰 답변", icon: MessageSquareQuote },
  study_note: { label: "학습 메모", icon: NotebookPen },
  study_mistake: { label: "오답", icon: BookOpen },
};
// 학습 메모·오답은 학습 기록이다. 경험이 아니므로 결과에 "경험 아님"을 붙인다.
const ALL: SearchKind[] = ["claim", "block", "answer", "study_note", "study_mistake"];

function semanticNote(note: string) {
  if (note === "no_embedding_route") return "임베딩 모델 미설정";
  if (note === "embedding_api_key_missing") return "임베딩 API 키 없음";
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
      <PageHeader title="검색" subtitle="확인한 주장, 올린 원문, 인터뷰 답변, 학습 메모·오답에서 찾습니다." />
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
      <div className="mb-6 flex flex-wrap gap-1.5">
        {ALL.map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={kinds.includes(k)}
            onClick={() => toggle(k)}
            className={cn(
              "h-9 rounded-full border px-3.5 text-[13px] transition-colors duration-150",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              kinds.includes(k)
                ? "border-fg bg-fg font-medium text-bg"
                : "border-line text-muted hover:border-fg/40 hover:text-fg",
            )}
          >
            {KIND_INFO[k].label}
          </button>
        ))}
      </div>

      {!q ? (
        <p className="text-sm text-muted">검색어를 넣어 주세요. 키워드와 의미 검색을 함께 씁니다.</p>
      ) : (
        <Loaded query={result}>
          {(r) => (
            <div className={result.isFetching ? "opacity-60" : undefined}>
              <p className="mb-2 text-[13px] text-muted tabular-nums">
                {r.hits.length}건 · {r.mode === "hybrid" ? "키워드 + 의미" : r.mode === "keyword" ? "키워드" : r.mode}
                {r.semantic_note ? ` · 의미 검색 안 함: ${semanticNote(r.semantic_note)}` : ""}
              </p>
              {r.expanded_terms?.length ? (
                <p className="mb-2 text-[13px] text-muted">
                  별칭으로 넓혀 찾음:{" "}
                  {r.expanded_terms.map((e, i) => (
                    <span key={e.term}>
                      {i ? " · " : ""}“{e.term}” → {e.spellings.join(", ")}
                    </span>
                  ))}{" "}
                  (설정 → 작업 공간의 기술명 별칭)
                </p>
              ) : null}
              {r.hits.length ? (
                <Hits hits={r.hits} />
              ) : (
                <p className="border-t border-line pt-3 text-sm text-muted">
                  찾은 결과가 없습니다. 다른 낱말로 찾거나 위의 종류를 넓혀 보세요.
                </p>
              )}
            </div>
          )}
        </Loaded>
      )}
    </div>
  );
}

const LINK = "inline-flex min-h-9 items-center font-medium underline-offset-2 hover:underline";

function Hits({ hits }: { hits: SearchHit[] }) {
  const [source, setSource] = useState<SourceTarget | null>(null);
  return (
    <>
      <ul className="divide-y divide-line border-t border-line">
        {hits.map((h) => {
          const info = KIND_INFO[h.kind] ?? KIND_INFO.claim;
          const Icon = info.icon;
          return (
            <li key={`${h.kind}-${h.id}`} className="py-3">
              <div className="mb-1 flex flex-wrap items-center gap-x-1.5 text-[13px] text-muted">
                <Icon size={14} aria-hidden />
                <span>{info.label}</span>
                {h.claim_status ? (
                  <span>· {STATUS_LABEL[h.claim_status as VerificationStatus] ?? h.claim_status}</span>
                ) : null}
                {h.kind === "block" ? <span>· 원문 그대로</span> : null}
                {h.record === "study" ? (
                  <span>
                    · <span className="font-medium text-fg">학습 기록 · 경험 아님</span>
                  </span>
                ) : null}
              </div>
              {h.question && h.kind === "answer" ? <p className="text-[13px] text-muted">질문: {h.question}</p> : null}
              <p className="text-sm leading-6 whitespace-pre-wrap">{h.snippet}</p>
              <div className="-mb-2 flex flex-wrap items-center gap-x-4 text-[13px] empty:hidden">
                {h.experience_id ? (
                  <Link to="/brain/$experienceId" params={{ experienceId: h.experience_id }} className={LINK}>
                    {h.experience_title ?? "경험"} 열기
                  </Link>
                ) : null}
                {h.kind === "block" && h.parse_run_id && h.block_id ? (
                  <button
                    type="button"
                    className={LINK}
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
                {h.record === "study" && h.study_item_id ? (
                  <Link to="/study/items/$itemId" params={{ itemId: h.study_item_id }} className={LINK}>
                    {h.study_title ?? "학습 항목"} 열기
                  </Link>
                ) : h.record === "study" && h.study_title ? (
                  <span className="inline-flex min-h-9 items-center text-muted">{h.study_title}</span>
                ) : null}
                {h.source_title ? (
                  <span className="inline-flex min-h-9 items-center text-muted">
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
