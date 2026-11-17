import type { Skill } from "@career-brain/client";
import { Button, cn, Input, Select, SelectItem } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { meErrorText } from "../lib/brainMe";

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const COLS = "sm:grid-cols-[minmax(0,1fr)_4rem_7.5rem_1.25rem]";
const CHIP =
  "inline-flex h-9 items-center rounded-full px-3.5 text-[13px] tabular-nums transition-colors duration-150 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

type Sort = "count" | "recent" | "name";
const SORTS: { value: Sort; label: string }[] = [
  { value: "count", label: "많이 쓴 순" },
  { value: "recent", label: "최근 쓴 순" },
  { value: "name", label: "이름순" },
];

const message = (e: unknown) => meErrorText(e) ?? errorText(e);

/**
 * 브레인 › 나 › 기술: 경험 태그·확인한 "사용한 기술" 사실·자격증에서 모은 기술. 따로 적지 않는다.
 * 같은 기술의 다른 표기(파이썬·Python)는 기술명 별칭 표로 묶는다. 합치기·나누기는 그 표를 고친다.
 */
export function SkillsPage() {
  const q = useQuery({ queryKey: ["skills"], queryFn: api.skills });
  const [sort, setSort] = useState<Sort>("count");
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="기술"
        subtitle="경험 태그·확인한 사실·자격증에서 모았습니다."
      />
      <Loaded query={q}>
        {(skills) => (
          <SkillList
            skills={skills}
            sort={sort}
            setSort={setSort}
            filter={filter}
            setFilter={setFilter}
            open={open}
            setOpen={setOpen}
          />
        )}
      </Loaded>
    </div>
  );
}

function SkillList({
  skills,
  sort,
  setSort,
  filter,
  setFilter,
  open,
  setOpen,
}: {
  skills: Skill[];
  sort: Sort;
  setSort: (s: Sort) => void;
  filter: string;
  setFilter: (s: string) => void;
  open: string | null;
  setOpen: (k: string | null) => void;
}) {
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const rows = f ? skills.filter((s) => s.spellings.some((x) => x.toLowerCase().includes(f))) : [...skills];
    if (sort === "recent") rows.sort((a, b) => (b.last_year ?? 0) - (a.last_year ?? 0) || b.count - a.count);
    else if (sort === "name") rows.sort((a, b) => a.name.localeCompare(b.name, "ko"));
    else rows.sort((a, b) => b.count - a.count || (b.last_year ?? 0) - (a.last_year ?? 0));
    return rows;
  }, [skills, sort, filter]);

  if (!skills.length)
    return (
      <p className="border-t border-line pt-3 text-sm text-muted">
        아직 모은 기술이 없습니다. 경험에 태그를 달거나 "사용한 기술" 사실을 확인하면 여기에 나옵니다.{" "}
        <Link to="/brain" hash="experiences" className="text-fg underline underline-offset-2">
          경험 보기
        </Link>
      </p>
    );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-1" role="radiogroup" aria-label="정렬">
          {SORTS.map((o) => (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={sort === o.value}
              onClick={() => setSort(o.value)}
              className={cn(
                CHIP,
                sort === o.value ? "bg-fg font-medium text-bg" : "text-muted hover:bg-subtle hover:text-fg",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
        <Input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="기술 찾기"
          aria-label="기술 찾기"
          className="w-full sm:ml-auto sm:w-48"
        />
      </div>
      <div className={cn("hidden gap-4 border-b border-line pb-2 text-xs text-muted sm:grid", COLS)}>
        <span>기술</span>
        <span className="text-right">경험</span>
        <span className="text-right">쓴 해</span>
        <span />
      </div>
      <ul className="divide-y divide-line border-b border-line max-sm:border-t">
        {shown.map((s) => (
          <SkillRow
            key={s.key}
            s={s}
            all={skills}
            open={open === s.key}
            onToggle={() => setOpen(open === s.key ? null : s.key)}
          />
        ))}
      </ul>
      {!shown.length ? <p className="pt-3 text-sm text-muted">"{filter}"에 맞는 기술이 없습니다.</p> : null}
    </div>
  );
}

function years(s: Skill): string | null {
  if (!s.last_year) return null;
  return s.first_year && s.first_year !== s.last_year ? `${s.first_year}–${s.last_year}` : String(s.last_year);
}

function SkillRow({ s, all, open, onToggle }: { s: Skill; all: Skill[]; open: boolean; onToggle: () => void }) {
  const others = s.spellings.filter((x) => x !== s.name);
  const used = s.count + s.credentials.length;
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(ROW, "grid w-full gap-0.5 text-left text-[13px] sm:items-center sm:gap-4", COLS)}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium">{s.name}</span>
          {others.length ? <span className="truncate text-muted max-sm:hidden">{others.join(", ")}</span> : null}
          <ChevronDown
            size={16}
            className={cn("ml-auto shrink-0 text-muted transition-transform sm:hidden", open && "rotate-180")}
          />
        </span>
        <span className="truncate text-muted tabular-nums sm:hidden">
          {[s.count ? `경험 ${s.count}` : null, s.credentials.length ? `자격 ${s.credentials.length}` : null, years(s)]
            .filter(Boolean)
            .join(" · ")}
        </span>
        <span className="hidden text-right tabular-nums sm:block">{used}</span>
        <span className="hidden text-right text-muted tabular-nums sm:block">{years(s) ?? "–"}</span>
        <ChevronDown
          size={16}
          className={cn("hidden shrink-0 text-muted transition-transform sm:block", open && "rotate-180")}
        />
      </button>
      {open ? <SkillDetail s={s} all={all} /> : null}
    </li>
  );
}

function SkillDetail({ s, all }: { s: Skill; all: Skill[] }) {
  const qc = useQueryClient();
  const [pick, setPick] = useState("");
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["skills"] });
    void qc.invalidateQueries({ queryKey: ["workspace-settings"] });
  };
  const merge = useMutation({
    mutationFn: () => api.mergeSkills(s.name, all.find((x) => x.key === pick)?.spellings.slice(0, 20) ?? [pick]),
    onSuccess: () => {
      setPick("");
      refresh();
    },
  });
  const split = useMutation({ mutationFn: (name: string) => api.splitSkill(name), onSuccess: refresh });
  const error = merge.error ?? split.error;
  const candidates = all.filter((x) => x.key !== s.key).sort((a, b) => a.name.localeCompare(b.name, "ko"));

  return (
    <div className="space-y-4 pb-4 pl-0 text-[13px] sm:pl-2">
      {s.experiences.length ? (
        <ul>
          {s.experiences.map((x) => (
            <li key={x.id} className="flex min-w-0 items-center gap-2">
              <Link
                to="/brain/$experienceId"
                params={{ experienceId: x.id }}
                className="inline-flex min-h-9 min-w-0 items-center truncate text-fg underline-offset-2 hover:underline"
              >
                {x.title}
              </Link>
              <span className="shrink-0 text-muted tabular-nums">
                {[
                  x.started_on ? x.started_on.slice(0, 7).replace("-", ".") : null,
                  x.ongoing ? "진행 중" : x.ended_on ? x.ended_on.slice(0, 7).replace("-", ".") : null,
                ]
                  .filter(Boolean)
                  .join(" – ")}
                {x.via.includes("claim") ? " · 확인한 사실" : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {s.credentials.length ? (
        <p className="text-muted">
          자격·어학:{" "}
          {s.credentials.map((c, i) => (
            <span key={c.id}>
              {i ? ", " : ""}
              <Link to="/brain/credentials" className="text-fg underline-offset-2 hover:underline">
                {c.name}
              </Link>
            </span>
          ))}
        </p>
      ) : null}

      <div className="space-y-2 border-t border-line pt-3">
        {s.merged.length ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-muted">합친 표기</span>
            {s.merged.map((m) => (
              <span key={m} className="inline-flex items-center gap-1">
                {m}
                <button
                  type="button"
                  className="inline-flex min-h-9 items-center px-1 text-muted underline underline-offset-2 hover:text-fg"
                  onClick={() => split.mutate(m)}
                  disabled={split.isPending}
                >
                  나누기
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {candidates.length ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (pick) merge.mutate();
            }}
          >
            <Select
              aria-label={`${s.name}에 합칠 기술`}
              value={pick}
              onValueChange={(v) => setPick(v)}
              className="w-full sm:w-56"
            >
              <SelectItem value="">같은 기술의 다른 표기 고르기</SelectItem>
              {candidates.map((x) => (
                <SelectItem key={x.key} value={x.key}>
                  {x.name}
                </SelectItem>
              ))}
            </Select>
            <Button type="submit" size="sm" className="max-sm:h-9" disabled={!pick || merge.isPending}>
              {s.name}(으)로 합치기
            </Button>
          </form>
        ) : null}
        {error ? (
          <p role="alert" className="text-danger">
            {message(error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
