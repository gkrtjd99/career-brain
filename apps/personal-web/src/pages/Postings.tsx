import type { PostingSummary } from "@career-brain/client";
import { Button, cn, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { AddPostings } from "../components/postings/AddPostings";
import { PostingRows, SeasonGroup } from "../components/postings/PostingRows";
import { api } from "../lib/api";
import { parseDeadline } from "../lib/dates";
import { seasonLabel } from "../lib/postings";

const route = getRouteApi("/postings");

/** 목록 보기: 접수 상태 넷 + 확인할 공고 + 보관 */
export type PostingsView = "open" | "upcoming" | "always" | "closed" | "attention" | "archived";
export const POSTINGS_VIEWS: readonly PostingsView[] = ["open", "upcoming", "always", "closed", "attention", "archived"];

const SEGMENTS: { value: PostingsView; label: string }[] = [
  { value: "open", label: "접수 중" },
  { value: "upcoming", label: "접수 예정" },
  { value: "always", label: "상시" },
  { value: "closed", label: "마감" },
];

const EMPTY: Record<PostingsView, string> = {
  open: "접수 중인 공고가 없습니다. 위에 공고 URL을 붙여넣으면 확인한 뒤 여기에 쌓입니다.",
  upcoming: "접수 시작 전인 공고가 없습니다. 공고에 접수 시작일이 적혀 있으면 여기에 모입니다.",
  always: "상시 채용 공고가 없습니다.",
  closed: "마감한 공고가 없습니다. 마감일이 지난 공고는 시기별로 여기에 모입니다.",
  attention: "확인할 공고가 없습니다.",
  archived: "보관한 공고가 없습니다.",
};

const time = (v: string | null | undefined, fallback: number) => {
  const d = v ? (v === "always" ? null : parseDeadline(v)) : null;
  return d ? d.getTime() : fallback;
};
const savedTime = (p: PostingSummary) => new Date(p.saved_at ?? p.created_at ?? 0).getTime();

/** 서버 정렬과 같다: 접수 중은 마감 가까운 순(마감일 모름은 끝), 예정은 시작 가까운 순, 마감은 최근 마감 순, 나머지는 최근 저장 순 */
function sortFor(view: PostingsView, items: PostingSummary[]) {
  const by = (f: (p: PostingSummary) => number) => [...items].sort((a, b) => f(a) - f(b));
  if (view === "open") return by((p) => (p.lifecycle === "unknown" ? Infinity : time(p.deadline, Infinity)));
  if (view === "upcoming") return by((p) => time(p.opens_at, Infinity));
  if (view === "closed") return by((p) => -time(p.deadline, 0));
  return by((p) => -savedTime(p));
}

function group(items: PostingSummary[]) {
  const saved = items.filter((p) => p.status === "saved");
  const lc = (p: PostingSummary) => p.lifecycle ?? "unknown";
  const draft = items.filter((p) => p.status === "draft");
  return {
    open: saved.filter((p) => lc(p) === "open" || lc(p) === "unknown"),
    upcoming: saved.filter((p) => lc(p) === "upcoming"),
    always: saved.filter((p) => lc(p) === "always"),
    closed: saved.filter((p) => lc(p) === "closed"),
    attention: draft,
    archived: items.filter((p) => p.status === "archived"),
    pending: draft.filter((p) => p.last_fetch_status === "pending").length,
    failed: draft.filter((p) => p.last_fetch_status === "failed").length,
  };
}

export function PostingsPage() {
  const { show: view = "open" } = route.useSearch();
  const navigate = route.useNavigate();
  const qc = useQueryClient();
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const setView = (v: PostingsView) => {
    setSelecting(false);
    setSelected(new Set());
    void navigate({ search: v === "open" ? {} : { show: v } });
  };

  const list = useQuery({
    queryKey: ["postings"],
    queryFn: api.postings,
    // 가져오는 중인 공고가 있으면 상태를 따라간다.
    refetchInterval: (q) => (q.state.data?.some((p) => p.last_fetch_status === "pending") ? 2500 : false),
  });
  const archive = useMutation({
    mutationFn: (ids: string[]) => (view === "archived" ? api.unarchivePostings(ids) : api.archivePostings(ids)),
    onSuccess: () => {
      setSelected(new Set());
      setSelecting(false);
      qc.invalidateQueries({ queryKey: ["postings"] });
    },
  });
  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="공고" />
      <AddPostings />

      <Loaded query={list}>
        {(items) => (
          <Body
            items={items}
            view={view}
            setView={setView}
            selecting={selecting}
            setSelecting={(on) => {
              setSelecting(on);
              if (!on) setSelected(new Set());
            }}
            selected={selected}
            toggle={toggle}
            fetching={list.isFetching}
          />
        )}
      </Loaded>

      {selecting ? (
        <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 mt-4 flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 shadow-[0_4px_16px_rgb(0_0_0/0.08)] md:bottom-4">
          <span className="min-w-0 flex-1 text-[13px] tabular-nums">
            {selected.size ? `${selected.size}개 고름` : "보관할 공고를 고르세요"}
          </span>
          <Button variant="ghost" onClick={() => setSelecting(false)}>
            취소
          </Button>
          <Button
            variant="primary"
            disabled={!selected.size || archive.isPending}
            onClick={() => archive.mutate([...selected])}
          >
            {archive.isPending ? <Spinner /> : null}
            {view === "archived" ? "보관 풀기" : "보관"}
          </Button>
        </div>
      ) : null}
      <ErrorText error={archive.error} className="mt-2" />
    </div>
  );
}

function Body({
  items,
  view,
  setView,
  selecting,
  setSelecting,
  selected,
  toggle,
  fetching,
}: {
  items: PostingSummary[];
  view: PostingsView;
  setView: (v: PostingsView) => void;
  selecting: boolean;
  setSelecting: (on: boolean) => void;
  selected: Set<string>;
  toggle: (id: string) => void;
  fetching: boolean;
}) {
  const g = useMemo(() => group(items), [items]);
  const shown = useMemo(() => sortFor(view, g[view]), [view, g]);
  const review = g.attention.length - g.pending - g.failed;
  const special = view === "attention" || view === "archived";

  return (
    <section className="mt-8">
      {g.attention.length && view !== "attention" ? (
        <p className="-mt-3 mb-4 flex flex-wrap items-center gap-x-1 text-[13px]">
          <button
            type="button"
            onClick={() => setView("attention")}
            className="-mx-1 inline-flex min-h-9 items-center rounded-md px-1 font-medium underline-offset-2 hover:underline"
          >
            {[
              review ? `확인할 공고 ${review}` : null,
              g.failed ? `가져오기 실패 ${g.failed}` : null,
              g.pending ? `가져오는 중 ${g.pending}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </button>
        </p>
      ) : null}

      <div className="mb-3 flex items-center gap-2">
        {special ? (
          <h2 className="min-w-0 flex-1 text-[15px] font-semibold">
            {view === "attention" ? "확인할 공고" : "보관한 공고"}{" "}
            <span className="font-normal text-muted tabular-nums">{shown.length}</span>
          </h2>
        ) : (
          <div
            role="radiogroup"
            aria-label="접수 상태"
            className="-mx-1 flex min-w-0 flex-1 gap-0.5 overflow-x-auto [scrollbar-width:none]"
          >
            {SEGMENTS.map((s) => (
              <button
                key={s.value}
                type="button"
                role="radio"
                aria-checked={view === s.value}
                onClick={() => setView(s.value)}
                className={cn(
                  "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[13px] whitespace-nowrap transition-colors duration-150",
                  view === s.value ? "bg-subtle font-medium text-fg" : "text-muted hover:text-fg",
                )}
              >
                {s.label}
                <span className="text-muted tabular-nums">{g[s.value].length}</span>
              </button>
            ))}
          </div>
        )}
        {fetching ? <Spinner /> : null}
        {special ? (
          <Button variant="ghost" size="sm" onClick={() => setView("open")}>
            목록으로
          </Button>
        ) : null}
        {shown.length && view !== "attention" && !selecting ? (
          <Button variant="ghost" size="sm" onClick={() => setSelecting(true)}>
            고르기
          </Button>
        ) : null}
      </div>

      {!shown.length ? (
        <p className="border-t border-line pt-3 text-sm text-muted">{EMPTY[view]}</p>
      ) : view === "closed" ? (
        <ClosedBySeason items={shown} selecting={selecting} selected={selected} toggle={toggle} />
      ) : (
        <PostingRows items={shown} selecting={selecting} selected={selected} onToggle={toggle} />
      )}

      {view === "attention" && shown.length ? (
        <p className="mt-3 text-[13px] text-muted">
          확인해서 저장한 공고만 지원 준비에 쓰입니다. 확인하지 않은 공고는 7일 뒤 정리됩니다.
        </p>
      ) : null}

      {g.archived.length && view !== "archived" ? (
        <div className="mt-6">
          <button
            type="button"
            onClick={() => setView("archived")}
            className="-mx-2 inline-flex min-h-9 items-center rounded-md px-2 text-[13px] text-muted hover:text-fg"
          >
            보관 <span className="ml-1 tabular-nums">{g.archived.length}</span>
          </button>
        </div>
      ) : null}
    </section>
  );
}

function ClosedBySeason({
  items,
  selecting,
  selected,
  toggle,
}: {
  items: PostingSummary[];
  selecting: boolean;
  selected: Set<string>;
  toggle: (id: string) => void;
}) {
  // items는 최근 마감 순: 시기도 최근부터
  const seasons: { key: string; rows: PostingSummary[] }[] = [];
  for (const p of items) {
    const key = p.season ?? "";
    const last = seasons[seasons.length - 1];
    const found = last?.key === key ? last : seasons.find((s) => s.key === key);
    if (found) found.rows.push(p);
    else seasons.push({ key, rows: [p] });
  }
  return (
    <div className="border-t border-line">
      {seasons.map((s) => (
        <SeasonGroup key={s.key} label={seasonLabel(s.key)} count={s.rows.length} defaultOpen={seasons.length === 1}>
          <PostingRows items={s.rows} selecting={selecting} selected={selected} onToggle={toggle} />
        </SeasonGroup>
      ))}
    </div>
  );
}
