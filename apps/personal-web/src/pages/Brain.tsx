import type { BrainOverview, ExperienceKind, ExperienceSummary } from "@career-brain/client";
import { Button, cn, Input, Select, Sheet } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus, Search as SearchIcon, Upload } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { ReferenceEntry } from "../components/companies/ReferenceEntry";
import { MeEntry } from "../components/credentials/MeEntry";
import { CreateExperienceForm, EMPTY_EXPERIENCE } from "../components/ExperienceForm";
import { ProfileEntry } from "../components/profile/ProfileEntry";
import { InterviewBatchButton } from "../components/runs/InterviewBatchSheet";
import { UploadSheet } from "../components/UploadSheet";
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
import { SourceInbox } from "./Sources";

const route = getRouteApi("/brain");

/** 한 번에 받는 경험 수 (서버 상한). 넘으면 거름을 좁히라고 알린다. */
const LIST_LIMIT = 1000;
/** 거름 줄에 보이는 태그 수 (자주 쓴 것부터) */
const TAG_CHIPS = 12;
const TAG_CHIPS_MOBILE = 6;

const CHIP =
  "inline-flex h-9 items-center rounded-full px-3.5 text-[13px] tabular-nums transition-colors duration-150 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const CHIP_ON = "bg-fg font-medium text-bg";
const CHIP_OFF = "text-muted hover:bg-subtle hover:text-fg";
const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const TEXT_LINK = "text-fg underline underline-offset-2";

export function BrainPage() {
  const search = route.useSearch();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const setSearch = (patch: Partial<BrainSearch>) =>
    navigate({
      to: "/brain",
      search: (prev: BrainSearch) => clean({ ...prev, ...patch }),
      replace: true,
    });

  const overview = useQuery({ queryKey: ["experiences", "overview"], queryFn: api.brainOverview });
  // 일괄 인터뷰는 거름과 무관하게 모든 경험에서 고른다 (오늘 화면과 같은 캐시)
  const all = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });

  const o = overview.data;
  const summary = o
    ? [
        `경험 ${o.experiences}`,
        ...EXPERIENCE_KINDS.filter((k) => o.kinds[k]).map((k) => `${EXPERIENCE_KIND_LABEL[k]} ${o.kinds[k]}`),
        o.pending_claims ? `확인할 주장 ${o.pending_claims}` : null,
      ].filter(Boolean)
    : [];

  return (
    <div>
      <PageHeader
        title="브레인"
        subtitle={
          <>
            <span className="tabular-nums">{summary.join(" · ")}</span>
            {summary.length ? <br className="sm:hidden" /> : null}
            {summary.length ? <span className="max-sm:hidden"> · </span> : null}
            확인한 주장만 문서와 검색 근거로 씁니다.
          </>
        }
        actions={
          <>
            <InterviewBatchButton experiences={all.data ?? []} />
            <Button onClick={() => setUploading(true)}>
              <Upload size={15} /> 자료 올리기
            </Button>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus size={15} /> 경험 추가
            </Button>
          </>
        }
      />

      <BrainSearchBox q={search.q ?? ""} onChange={(q) => setSearch({ q: q || undefined })} />

      {o && o.needs_review > 0 && !filtersActive(search) ? (
        <ReviewBlock overview={o} onShowAll={() => setSearch({ review: true })} />
      ) : null}
      {!filtersActive(search) ? <SourceInbox /> : null}

      <ErrorText error={overview.error} className="mb-3" />
      {o && o.experiences === 0 ? (
        <Quiet>
          아직 경험이 없습니다. 자료를 올리거나 경험을 직접 추가하세요.{" "}
          <button type="button" onClick={() => setUploading(true)} className={TEXT_LINK}>
            자료 올리기
          </button>{" "}
          ·{" "}
          <button type="button" onClick={() => setCreating(true)} className={TEXT_LINK}>
            경험 추가
          </button>
        </Quiet>
      ) : o ? (
        <>
          <Filters search={search} overview={o} setSearch={setSearch} />
          <ExperienceResults search={search} setSearch={setSearch} />
        </>
      ) : null}

      <MeEntry />
      <ProfileEntry />
      <ReferenceEntry />
      <Link
        to="/brain/sources"
        className="mt-10 inline-flex min-h-9 items-center gap-0.5 text-[13px] text-muted hover:text-fg"
      >
        올린 자료 모두 보기 <ChevronRight size={14} />
      </Link>

      <CreateExperienceSheet open={creating} onClose={() => setCreating(false)} />
      <UploadSheet open={uploading} onClose={() => setUploading(false)} />
    </div>
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
  return !!(s.q || s.kind || s.tag?.length || s.year || s.review);
}

/** 브레인 안 찾기: 경험의 제목·조직·역할·태그. 주장·원문 문단은 검색 화면으로. */
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
        <Button type="submit" aria-label="찾기">
          <SearchIcon size={16} />
        </Button>
      </form>
      <Link
        to="/search"
        search={draft.trim() ? { q: draft.trim() } : {}}
        className="mt-1.5 inline-flex min-h-9 items-center text-[13px] text-muted hover:text-fg"
      >
        주장·문단까지 검색 <ChevronRight size={14} />
      </Link>
    </div>
  );
}

/** 맨 위 "확인할 것": AI가 뽑은 주장 후보가 있는 경험. */
function ReviewBlock({ overview, onShowAll }: { overview: BrainOverview; onShowAll: () => void }) {
  const q = useQuery({
    queryKey: ["experiences", "review-top"],
    queryFn: () => api.experiences({ status: "needs_review", sort: "updated", limit: 3 }),
  });
  return (
    <section className="mb-8">
      <h2 className="text-[15px] font-semibold">
        확인할 것 <span className="font-normal text-muted tabular-nums">{overview.pending_claims}</span>
      </h2>
      <p className="mt-0.5 text-[13px] text-muted">AI가 원문에서 뽑은 주장 후보입니다. 확인하기 전에는 근거로 쓰지 않습니다.</p>
      <ul className="mt-3 divide-y divide-line border-t border-line">
        {(q.data ?? []).map((e) => (
          <li key={e.id}>
            <Link
              to="/brain/$experienceId"
              params={{ experienceId: e.id }}
              className={cn(ROW, "flex items-center gap-4")}
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{e.title}</span>
              <span className="shrink-0 text-[13px] font-semibold tabular-nums">확인 필요 {e.needs_review}</span>
            </Link>
          </li>
        ))}
        {overview.needs_review > (q.data?.length ?? 0) ? (
          <li>
            <button
              type="button"
              onClick={onShowAll}
              className="inline-flex min-h-9 items-center py-2 text-[13px] text-muted hover:text-fg"
            >
              확인할 경험 {overview.needs_review}개 모두 보기 <ChevronRight size={14} />
            </button>
          </li>
        ) : null}
      </ul>
    </section>
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

  return (
    <div className="mb-6 space-y-2">
      <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label="종류">
        {[{ k: undefined as ExperienceKind | undefined, label: "전체", n: overview.experiences }, ...kinds.map((k) => ({ k, label: EXPERIENCE_KIND_LABEL[k], n: overview.kinds[k] }))].map(
          (f) => (
            <button
              key={f.k ?? "all"}
              type="button"
              role="radio"
              aria-checked={search.kind === f.k}
              onClick={() => setSearch({ kind: f.k })}
              className={cn(CHIP, search.kind === f.k ? CHIP_ON : CHIP_OFF)}
            >
              {f.label}
              <span className={cn("ml-1.5", search.kind === f.k ? "text-bg/70" : "text-muted")}>{f.n}</span>
            </button>
          ),
        )}
      </div>
      {tagChips.length ? (
        <div className="flex flex-wrap items-center gap-1" aria-label="태그">
          {tagChips.map((t, i) => (
            <button
              key={t}
              type="button"
              aria-pressed={isOn(t)}
              onClick={() => toggleTag(t)}
              className={cn(
                CHIP,
                "border",
                isOn(t) ? "border-fg " + CHIP_ON : "border-line " + CHIP_OFF,
                // 좁은 화면에서는 자주 쓴 6개만 (고른 태그는 늘 보인다)
                i >= TAG_CHIPS_MOBILE && !isOn(t) && "max-sm:hidden",
              )}
            >
              {t}
            </button>
          ))}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {years.length ? (
          <Select
            aria-label="연도"
            value={search.year ?? ""}
            onChange={(e) => setSearch({ year: e.target.value ? Number(e.target.value) : undefined })}
            className="w-auto tabular-nums"
          >
            <option value="">모든 연도</option>
            {years.map((y) => (
              <option key={y} value={y}>
                {y}년
              </option>
            ))}
          </Select>
        ) : null}
        <button
          type="button"
          aria-pressed={!!search.review}
          onClick={() => setSearch({ review: search.review ? undefined : true })}
          className={cn(CHIP, search.review ? CHIP_ON : CHIP_OFF)}
        >
          확인 필요만{overview.needs_review ? <span className="ml-1.5 tabular-nums">{overview.needs_review}</span> : null}
        </button>
        {filtersActive(search) ? (
          <button
            type="button"
            onClick={() => setSearch({ q: undefined, kind: undefined, tag: undefined, year: undefined, review: undefined })}
            className="inline-flex min-h-9 items-center text-[13px] text-muted underline underline-offset-2 hover:text-fg"
          >
            필터 초기화
          </button>
        ) : null}
        <div className="ml-auto flex items-center gap-1" role="radiogroup" aria-label="보기">
          {[
            { v: undefined, label: "타임라인" },
            { v: "table" as const, label: "표" },
          ].map((f) => (
            <button
              key={f.label}
              type="button"
              role="radio"
              aria-checked={search.view === f.v}
              onClick={() => setSearch({ view: f.v })}
              className={cn(CHIP, search.view === f.v ? CHIP_ON : CHIP_OFF)}
            >
              {f.label}
            </button>
          ))}
        </div>
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
    // 연표는 시작 달 순서, 표는 고른 정렬
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
              {search.review ? "확인할 주장이 있는 경험이 없습니다." : "조건에 맞는 경험이 없습니다."}{" "}
              <Link to="/brain" search={{}} className={TEXT_LINK}>
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

/** 연표: 시작 연도별 (최근 먼저), 시작 달이 없는 경험은 맨 끝. 경력 아래 프로젝트는 접어 둔다. */
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
        <div className="truncate text-[13px] text-muted tabular-nums">{meta(e, { withParent })}</div>
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
  /** 거름을 걸었을 때만 */
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
          onChange={(e) => onSort((e.target.value || undefined) as BrainSearch["sort"])}
          className="w-auto"
        >
          <option value="">시작 달</option>
          <option value="updated">최근 수정순</option>
          <option value="title">제목</option>
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
      {!e.needs_review && !confirmed ? <div className="text-muted">주장 없음</div> : null}
    </div>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-line pt-3 text-sm text-muted">{children}</p>;
}

function CreateExperienceSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="경험 추가">
      {open ? <CreateExperienceForm initial={EMPTY_EXPERIENCE} onDone={onClose} /> : null}
    </Sheet>
  );
}
