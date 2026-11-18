import type { BrainOverview, ExperienceKind, ExperienceSummary } from "@career-brain/client";
import { Button, Chip, cn, FilterChips, IconButton, Input, Select, SelectItem, ViewTabs } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus, Search as SearchIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ErrorText, PageHeader, Loaded } from "../components/common";
import { useIngest } from "../components/GlobalActions";
import { InterviewBatchButton } from "../components/runs/InterviewBatchSheet";
import { api } from "../lib/api";
import {
  confirmedCount,
  EXPERIENCE_KIND_LABEL,
  EXPERIENCE_KINDS,
  groupByYear,
  nest,
  periodLabel,
  type ExperienceNode,
} from "../lib/experience";
import { ORIGIN_LABEL } from "../lib/labels";
import type { BrainSearch } from "../router";

const route = getRouteApi("/brain/experiences");

/** 한 번에 받는 경험 수 (서버 상한). 넘으면 필터를 좁히라고 알린다. */
const LIST_LIMIT = 1000;
/** 필터 줄에 보이는 태그 수 (자주 쓴 것부터) */
const TAG_CHIPS = 12;
const TAG_CHIPS_MOBILE = 6;

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const TEXT_LINK = "inline-flex min-h-9 items-center text-fg underline underline-offset-2 sm:min-h-6";

/**
 * 브레인 › 경험 모두 보기: 연도별 타임라인과 표, 종류·태그·연도·확인 상태 필터, 찾기.
 * 브레인 첫 화면(이력서 꼴)의 경력·프로젝트 "모두 보기"로 들어온다.
 */
export function BrainExperiencesPage() {
  const search = route.useSearch();
  const navigate = useNavigate();
  const ingest = useIngest();
  const setSearch = (patch: Partial<BrainSearch>) =>
    navigate({
      to: "/brain/experiences",
      search: (prev: BrainSearch) => clean({ ...prev, ...patch }),
      replace: true,
    });
  const overview = useQuery({ queryKey: ["experiences", "overview"], queryFn: api.brainOverview });
  const all = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });
  const o = overview.data;
  return (
    <>
      <PageHeader
        title="경험"
        back={{ to: "/brain", label: "브레인" }}
        actions={
          <>
            <InterviewBatchButton experiences={all.data ?? []} />
            <Button variant="primary" onClick={() => ingest.openExperience()}>
              <Plus size={15} /> 경험 추가
            </Button>
          </>
        }
      />
      <BrainSearchBox q={search.q ?? ""} onChange={(q) => setSearch({ q: q || undefined })} />
      <ErrorText error={overview.error} className="mb-3" />
      {o && o.experiences === 0 ? (
        <Quiet>
          아직 경험이 없습니다. 이력서나 경력기술서를 넣거나 경험을 직접 추가하세요.{" "}
          <button type="button" onClick={() => ingest.open()} className={TEXT_LINK}>
            브레인에 넣기
          </button>
        </Quiet>
      ) : o ? (
        <>
          <Filters search={search} overview={o} setSearch={setSearch} />
          <ExperienceResults search={search} setSearch={setSearch} />
        </>
      ) : null}
    </>
  );
}

/** 빈 값을 빼서 주소를 짧게 둔다. */
function clean(s: BrainSearch): BrainSearch {
  const out: BrainSearch = {};
  if (s.q?.trim()) out.q = s.q.trim();
  if (s.view === "table") out.view = "table";
  if (s.kind) out.kind = s.kind;
  if (s.tag?.length) out.tag = s.tag;
  if (s.year) out.year = s.year;
  if (s.review) out.review = true;
  if (s.sort) out.sort = s.sort;
  return out;
}

function filtersActive(s: BrainSearch) {
  return !!(s.q || s.kind || s.tag?.length || s.year || s.review || s.dup);
}

/** 브레인 안 찾기: 경험의 제목·조직·역할·태그. 확인한 사실·원문 문단은 근거 검색으로. */
function BrainSearchBox({ q, onChange }: { q: string; onChange: (q: string) => void }) {
  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);
  // 치는 동안 주소를 조금 늦게 바꾼다 (입력마다 목록을 다시 부르지 않게)
  useEffect(() => {
    if (draft.trim() === q) return;
    const t = setTimeout(() => onChange(draft.trim()), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);
  return (
    <div className="mb-6">
      <form
        role="search"
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          onChange(draft.trim());
        }}
      >
        <Input
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="경험 찾기: 제목·조직·역할·태그"
          aria-label="브레인에서 경험 찾기"
          maxLength={200}
        />
        <IconButton type="submit" variant="secondary" label="찾기">
          <SearchIcon size={16} />
        </IconButton>
      </form>
      <Link
        to="/search"
        search={draft.trim() ? { q: draft.trim() } : {}}
        className="mt-1.5 inline-flex min-h-9 items-center text-[13px] text-muted hover:text-fg sm:min-h-7"
      >
        근거 전체 검색 <ChevronRight size={14} />
      </Link>
    </div>
  );
}

function Filters({
  search,
  overview,
  setSearch,
}: {
  search: BrainSearch;
  overview: BrainOverview;
  setSearch: (patch: Partial<BrainSearch>) => void;
}) {
  const selectedTags = search.tag ?? [];
  const topTags = overview.tags.slice(0, TAG_CHIPS).map((t) => t.tag);
  const lower = new Set(topTags.map((t) => t.toLowerCase()));
  const tagChips = [...topTags, ...selectedTags.filter((t) => !lower.has(t.toLowerCase()))];
  const isOn = (t: string) => selectedTags.some((x) => x.toLowerCase() === t.toLowerCase());
  const toggleTag = (t: string) =>
    setSearch({
      tag: isOn(t) ? selectedTags.filter((x) => x.toLowerCase() !== t.toLowerCase()) : [...selectedTags, t],
    });
  const years: number[] = [];
  if (overview.year_min !== null && overview.year_max !== null)
    for (let y = overview.year_max; y >= overview.year_min; y--) years.push(y);
  const kinds = EXPERIENCE_KINDS.filter((k) => overview.kinds[k] || search.kind === k);
  const dups = useQuery({
    queryKey: ["experiences", "duplicates"],
    queryFn: () => api.experiences({ duplicates: true, limit: 200 }),
  });
  const dupCount = dups.data?.length ?? 0;

  return (
    <div className="mb-6 space-y-2">
      <FilterChips<ExperienceKind | undefined>
        label="종류"
        value={search.kind}
        onChange={(kind) => setSearch({ kind })}
        items={[
          { value: undefined, label: "전체", count: overview.experiences },
          ...kinds.map((k) => ({ value: k, label: EXPERIENCE_KIND_LABEL[k], count: overview.kinds[k] })),
        ]}
      />
      {tagChips.length ? (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="태그">
          {tagChips.map((t, i) => (
            <Chip
              key={t}
              pressed={isOn(t)}
              onClick={() => toggleTag(t)}
              // 좁은 화면에서는 자주 쓴 6개만 (고른 태그는 늘 보인다)
              className={cn(i >= TAG_CHIPS_MOBILE && !isOn(t) && "max-sm:hidden")}
            >
              {t}
            </Chip>
          ))}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {years.length ? (
          <Select
            aria-label="연도"
            value={search.year ?? ""}
            onValueChange={(v) => setSearch({ year: v ? Number(v) : undefined })}
            className="w-auto tabular-nums"
          >
            <SelectItem value="">모든 연도</SelectItem>
            {years.map((y) => (
              <SelectItem key={y} value={y}>
                {y}년
              </SelectItem>
            ))}
          </Select>
        ) : null}
        <Chip
          pressed={!!search.review}
          onClick={() => setSearch({ review: search.review ? undefined : true })}
          count={overview.needs_review || null}
        >
          확인 필요만
        </Chip>
        {dupCount || search.dup ? (
          <Chip pressed={!!search.dup} onClick={() => setSearch({ dup: search.dup ? undefined : true })} count={dupCount || null}>
            같은 경험일 수 있음
          </Chip>
        ) : null}
        {filtersActive(search) ? (
          <button
            type="button"
            onClick={() =>
              setSearch({ q: undefined, kind: undefined, tag: undefined, year: undefined, review: undefined, dup: undefined })
            }
            className="inline-flex min-h-9 items-center text-[13px] text-muted underline underline-offset-2 hover:text-fg sm:min-h-7"
          >
            필터 초기화
          </button>
        ) : null}
        <ViewTabs<"timeline" | "table">
          label="보기"
          value={search.view === "table" ? "table" : "timeline"}
          onChange={(v) => setSearch({ view: v === "table" ? "table" : undefined })}
          items={[
            { value: "timeline", label: "타임라인" },
            { value: "table", label: "표" },
          ]}
          className="ml-auto border-b-0"
        />
      </div>
    </div>
  );
}

function ExperienceResults({
  search,
  setSearch,
}: {
  search: BrainSearch;
  setSearch: (patch: Partial<BrainSearch>) => void;
}) {
  const table = search.view === "table";
  const query = {
    q: search.q,
    kind: search.kind ? [search.kind] : undefined,
    tags: search.tag,
    year: search.year,
    status: search.review ? ("needs_review" as const) : undefined,
    duplicates: search.dup || undefined,
    // 타임라인은 시작 달 순서, 표는 고른 정렬
    sort: table ? (search.sort ?? "start") : ("start" as const),
    limit: LIST_LIMIT,
  };
  const q = useQuery({
    queryKey: ["experiences", "list", query],
    queryFn: ({ signal }) => api.experiencePage(query, signal),
    placeholderData: keepPreviousData,
  });
  const active = filtersActive(search);
  return (
    <Loaded query={q}>
      {({ items, total }) => {
        if (!items.length)
          return (
            <Quiet>
              {search.review ? "확인할 사실이 있는 경험이 없습니다." : "조건에 맞는 경험이 없습니다."}{" "}
              <Link to="/brain/experiences" search={{}} className={TEXT_LINK}>
                필터 초기화
              </Link>
            </Quiet>
          );
        const nodes = nest(items);
        return (
          <div className={q.isFetching ? "opacity-60" : undefined}>
            {table ? (
              <ExperienceTable
                nodes={nodes}
                sort={search.sort}
                onSort={(sort) => setSearch({ sort })}
                count={active ? total : null}
              />
            ) : (
              <>
                {active ? <p className="mb-2 text-[13px] text-muted tabular-nums">{total}개 경험</p> : null}
                <Timeline nodes={nodes} expandAll={active} />
              </>
            )}
            {total > items.length ? (
              <p className="mt-3 text-[13px] text-muted tabular-nums">
                앞 {items.length}개만 보입니다 (모두 {total}개). 필터를 좁혀 보세요.
              </p>
            ) : null}
          </div>
        );
      }}
    </Loaded>
  );
}

/** 접힌 자식들의 확인할 주장 수 (부모 행에서 놓치지 않게) */
function childReview(children: ExperienceSummary[]) {
  return children.reduce((n, c) => n + c.needs_review, 0);
}

function childrenLabel(children: ExperienceSummary[]) {
  return children.every((c) => c.kind === "project") ? "프로젝트" : "안의 경험";
}

function meta(e: ExperienceSummary, { withKind = true, withParent = false } = {}) {
  return [
    withKind ? EXPERIENCE_KIND_LABEL[e.kind] : null,
    withParent && e.parent_title ? `‹ ${e.parent_title}` : null,
    e.organization,
    e.role,
    periodLabel(e),
    e.origin === "ai_extraction" ? ORIGIN_LABEL[e.origin] : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** 타임라인: 시작 연도별 (최근 먼저), 시작 달이 없는 경험은 맨 끝. 경력 아래 프로젝트는 접어 둔다. */
function Timeline({ nodes, expandAll }: { nodes: ExperienceNode[]; expandAll: boolean }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <div className="space-y-8">
      {groupByYear(nodes).map((g) => (
        <section key={g.year ?? "none"} aria-label={g.year ? `${g.year}년` : "기간 없음"}>
          <h2 className="mb-1 text-[13px] font-semibold text-muted tabular-nums">{g.year ?? "기간 없음"}</h2>
          <ul className="divide-y divide-line border-t border-line">
            {g.nodes.map(({ item, children }) => {
              const expanded = expandAll ? !open.has(item.id) : open.has(item.id);
              return (
                <li key={item.id}>
                  <div className="flex items-center gap-2">
                    <ExperienceRow e={item} withParent className="min-w-0 flex-1" />
                    {children.length ? (
                      <button
                        type="button"
                        aria-expanded={expanded}
                        aria-label={`${item.title}: ${childrenLabel(children)} ${children.length}개 ${expanded ? "접기" : "펼치기"}`}
                        onClick={() => toggle(item.id)}
                        className="inline-flex h-9 shrink-0 items-center gap-0.5 rounded-md px-2 text-[13px] text-muted tabular-nums hover:bg-subtle hover:text-fg"
                      >
                        <span className="max-sm:hidden">{childrenLabel(children)}</span> {children.length}
                        {!expanded && childReview(children) ? (
                          <span className="ml-1 font-semibold text-fg">확인 {childReview(children)}</span>
                        ) : null}
                        <ChevronRight
                          size={14}
                          className={cn("transition-transform duration-150", expanded && "rotate-90")}
                        />
                      </button>
                    ) : null}
                  </div>
                  {expanded && children.length ? (
                    <ul className="mb-2 ml-2 divide-y divide-line border-l border-line pl-3 sm:ml-3 sm:pl-4">
                      {children.map((c) => (
                        <li key={c.id}>
                          <ExperienceRow e={c} />
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ExperienceRow({
  e,
  withParent = false,
  className,
}: {
  e: ExperienceSummary;
  withParent?: boolean;
  className?: string;
}) {
  return (
    <Link
      to="/brain/$experienceId"
      params={{ experienceId: e.id }}
      className={cn(ROW, "flex items-center gap-4", className)}
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{e.title}</div>
        <div className="line-clamp-2 text-[13px] text-muted tabular-nums">{meta(e, { withParent })}</div>
        {e.tags.length ? (
          <div className="truncate text-[13px] text-muted">{e.tags.slice(0, 6).join(" · ")}</div>
        ) : null}
      </div>
      <ClaimState e={e} />
    </Link>
  );
}

/** 표: 자체 가로 스크롤 (390px 폭에서 화면 밖으로 넘치지 않게). 자식은 부모 바로 아래 들여 쓴다. */
function ExperienceTable({
  nodes,
  sort,
  onSort,
  count,
}: {
  nodes: ExperienceNode[];
  sort: BrainSearch["sort"];
  onSort: (sort: BrainSearch["sort"]) => void;
  /** 필터를 걸었을 때만 */
  count: number | null;
}) {
  const rows = nodes.flatMap(({ item, children }) => [
    { e: item, child: false },
    ...children.map((c) => ({ e: c, child: true })),
  ]);
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <p className="mr-auto text-[13px] text-muted tabular-nums">{count !== null ? `${count}개 경험` : null}</p>
        <label htmlFor="brain-sort" className="text-[13px] text-muted">
          정렬
        </label>
        <Select
          id="brain-sort"
          value={sort ?? ""}
          onValueChange={(v) => onSort((v || undefined) as BrainSearch["sort"])}
          className="w-auto"
        >
          <SelectItem value="">시작 달</SelectItem>
          <SelectItem value="updated">최근 수정순</SelectItem>
          <SelectItem value="title">제목</SelectItem>
        </Select>
      </div>
      <div className="overflow-x-auto overscroll-x-contain border-t border-line">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="border-b border-line text-[13px] text-muted">
            <tr>
              {["제목", "종류", "조직", "기간", "태그", "확인 상태"].map((h) => (
                <th key={h} scope="col" className="px-2 py-2 font-medium whitespace-nowrap first:pl-0">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map(({ e, child }) => (
              <tr key={e.id} className="transition-colors duration-150 hover:bg-subtle/60">
                <td className={cn("max-w-[260px] py-2.5 pr-2", child ? "pl-5" : "pl-0")}>
                  <Link
                    to="/brain/$experienceId"
                    params={{ experienceId: e.id }}
                    className="flex min-h-9 items-center font-medium hover:underline"
                  >
                    {child ? <span className="mr-1.5 text-muted" aria-hidden>└</span> : null}
                    <span className="truncate">{e.title}</span>
                  </Link>
                  {!child && e.parent_title ? (
                    <div className="truncate text-[13px] text-muted">‹ {e.parent_title}</div>
                  ) : null}
                </td>
                <td className="px-2 py-2.5 whitespace-nowrap text-muted">{EXPERIENCE_KIND_LABEL[e.kind]}</td>
                <td className="max-w-[160px] truncate px-2 py-2.5 text-muted">{e.organization ?? "–"}</td>
                <td className="px-2 py-2.5 whitespace-nowrap text-muted tabular-nums">{periodLabel(e) ?? "–"}</td>
                <td className="max-w-[180px] truncate px-2 py-2.5 text-muted">{e.tags.join(", ") || "–"}</td>
                <td className="px-2 py-2.5 whitespace-nowrap">
                  <ClaimState e={e} inline />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 오른쪽 상태: 확인할 것이 있으면 진하게, 나머지는 회색 한 줄. */
function ClaimState({ e, inline = false }: { e: ExperienceSummary; inline?: boolean }) {
  const confirmed = confirmedCount(e);
  return (
    <div
      className={cn(
        "shrink-0 text-[13px] leading-tight tabular-nums",
        inline ? "flex gap-2" : "text-right",
      )}
    >
      {e.needs_review ? <div className="font-semibold">확인 필요 {e.needs_review}</div> : null}
      {confirmed ? <div className="text-muted">확인됨 {confirmed}</div> : null}
      {!e.needs_review && !confirmed ? <div className="text-muted">사실 없음</div> : null}
      {e.possible_duplicate_of ? <div className="font-medium text-warn">같은 경험일 수 있음</div> : null}
    </div>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}
