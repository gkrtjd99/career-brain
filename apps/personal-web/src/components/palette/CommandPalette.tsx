import type { SearchHit } from "@career-brain/client";
import { cn, CommandDialog, CommandDialogClose, Spinner } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  BookOpen,
  Brain,
  Briefcase,
  Building2,
  CalendarClock,
  FileText,
  Files,
  FolderInput,
  GraduationCap,
  House,
  KanbanSquare,
  Library,
  Link2,
  Plus,
  Quote,
  Search,
  SlidersHorizontal,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { api } from "../../lib/api";
import { dateKey } from "../../lib/dates";
import { hostOf } from "../../lib/format";
import { useIngest } from "../GlobalActions";

/** 한 갈래에 보이는 수 (이동은 종류마다 이만큼, 모두 합쳐 MOVE_CAP) */
const PER_KIND = 3;
const MOVE_CAP = 8;
const EVIDENCE_CAP = 3;
/** 치는 동안 서버에 묻기까지 기다리는 시간 */
const DEBOUNCE_MS = 200;

type SectionKey = "go" | "do" | "evidence";
const SECTION_LABEL: Record<SectionKey, string> = { go: "이동", do: "할 일", evidence: "근거에서 찾기" };

interface Option {
  id: string;
  section: SectionKey;
  icon: LucideIcon;
  label: ReactNode;
  /** 오른쪽 회색 설명 (종류·회사 등) */
  hint?: string;
  /** 고르기 (팔레트는 이미 닫힌 뒤) */
  run: () => void;
}

/** 대소문자·띄어쓰기를 무시하고 모든 낱말이 들어 있으면 맞다 */
function matches(q: string, ...fields: (string | null | undefined)[]) {
  const hay = fields.filter(Boolean).join(" ").toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
}

function useDebounced(value: string, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** 화면이 그려진 뒤 그 칸에 초점을 준다 (다른 화면으로 옮긴 직후). 2초 안에 못 찾으면 그만둔다. */
export function focusWhenReady(selector: string) {
  const until = performance.now() + 2000;
  const tick = () => {
    const el = document.querySelector<HTMLElement>(selector);
    if (el) {
      el.focus();
      el.scrollIntoView({ block: "center" });
    } else if (performance.now() < until) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

const EVIDENCE_KIND: Record<string, string> = {
  claim: "사실",
  block: "원문",
  answer: "인터뷰 답변",
  study_note: "학습 메모",
  study_mistake: "오답",
};

const PAGES: { to: string; label: string; icon: LucideIcon; words: string }[] = [
  { to: "/", label: "오늘", icon: House, words: "today 일정 달력" },
  { to: "/postings", label: "공고", icon: Briefcase, words: "posting 채용" },
  { to: "/applications", label: "지원", icon: KanbanSquare, words: "application 지원서" },
  { to: "/brain", label: "브레인", icon: Brain, words: "brain 경험" },
  { to: "/study", label: "학습", icon: GraduationCap, words: "study 복습 오답" },
  { to: "/brain/sources", label: "받은 자료", icon: FolderInput, words: "자료함 원본 파일" },
  { to: "/documents", label: "기본 문서", icon: Files, words: "공용 문서 이력서 포트폴리오" },
  { to: "/manage", label: "연결·관리", icon: SlidersHorizontal, words: "설정 모델 토큰 백업" },
];

/**
 * 명령 팔레트 (⌘K): 이동 · 할 일 · 근거에서 찾기. 입력 칸이 combobox, 아래 목록이 listbox.
 * ↑↓로 옮기고 Enter로 고르며 Esc로 닫는다. 목록은 이미 있는 목록 API를 그대로 읽는다.
 */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [q, setQ] = useState("");
  // 닫을 때 입력을 비운다 (다음에 열면 처음부터)
  useEffect(() => {
    if (!open) setQ("");
  }, [open]);
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="검색과 이동" description="화면 이동, 할 일, 근거 검색">
      {open ? <PaletteBody q={q} setQ={setQ} close={() => onOpenChange(false)} /> : null}
    </CommandDialog>
  );
}

function PaletteBody({ q, setQ, close }: { q: string; setQ: (q: string) => void; close: () => void }) {
  const navigate = useNavigate();
  const ingest = useIngest();
  const listId = useId();
  const term = q.trim();
  const debounced = useDebounced(term, DEBOUNCE_MS);
  const typing = term !== debounced;

  // 작은 목록은 한 번 받아 브라우저에서 거른다 (다른 화면과 같은 캐시)
  const postings = useQuery({ queryKey: ["postings"], queryFn: api.postings, staleTime: 60_000 });
  const applications = useQuery({ queryKey: ["applications"], queryFn: api.applications, staleTime: 60_000 });
  const shared = useQuery({ queryKey: ["shared-documents"], queryFn: api.sharedDocuments, staleTime: 60_000 });
  // 경험·회사·참고 글은 서버가 거른다
  const experiences = useQuery({
    queryKey: ["experiences", "palette", debounced],
    queryFn: () => api.experiences({ q: debounced, limit: PER_KIND }),
    enabled: !!debounced,
    staleTime: 30_000,
  });
  const companies = useQuery({
    queryKey: ["companies", debounced],
    queryFn: () => api.companies(debounced),
    enabled: !!debounced,
    staleTime: 30_000,
  });
  const references = useQuery({
    queryKey: ["references", "palette", debounced],
    queryFn: () => api.references({ q: debounced }),
    enabled: !!debounced,
    staleTime: 30_000,
  });
  const evidence = useQuery({
    queryKey: ["search", "palette", debounced],
    queryFn: ({ signal }) => api.search(debounced, { limit: EVIDENCE_CAP }, signal),
    enabled: debounced.length >= 2,
    staleTime: 60_000,
    retry: false,
  });
  const busy =
    typing ||
    (!!debounced && (experiences.isFetching || companies.isFetching || references.isFetching || evidence.isFetching));

  const options = useMemo<Option[]>(() => {
    const go = (fn: () => void) => () => {
      close();
      fn();
    };
    const out: Option[] = [];

    // ---- 이동
    const move: Option[] = [];
    for (const p of PAGES)
      if (!term || matches(term, p.label, p.words))
        move.push({
          id: `page-${p.to}`,
          section: "go",
          icon: p.icon,
          label: p.label,
          hint: "화면",
          run: go(() => void navigate({ to: p.to })),
        });
    if (term) {
      const take = <T,>(rows: T[] | undefined, pick: (r: T) => boolean) => (rows ?? []).filter(pick).slice(0, PER_KIND);
      for (const p of take(postings.data, (p) => matches(term, p.title, p.company, p.canonical_url)))
        move.push({
          id: `posting-${p.id}`,
          section: "go",
          icon: Briefcase,
          label: p.title ?? hostOf(p.canonical_url),
          hint: ["공고", p.company].filter(Boolean).join(" · "),
          run: go(() => void navigate({ to: "/postings/$postingId", params: { postingId: p.id } })),
        });
      for (const a of take(applications.data, (a) => matches(term, a.title, a.company, a.job_title)))
        move.push({
          id: `app-${a.id}`,
          section: "go",
          icon: KanbanSquare,
          label: a.title,
          hint: ["지원", a.company].filter(Boolean).join(" · "),
          run: go(() => void navigate({ to: "/applications/$applicationId", params: { applicationId: a.id } })),
        });
      for (const e of (debounced === term ? experiences.data : undefined)?.slice(0, PER_KIND) ?? [])
        move.push({
          id: `exp-${e.id}`,
          section: "go",
          icon: Brain,
          label: e.title,
          hint: ["경험", e.organization].filter(Boolean).join(" · "),
          run: go(() => void navigate({ to: "/brain/$experienceId", params: { experienceId: e.id } })),
        });
      for (const c of (debounced === term ? companies.data : undefined)?.slice(0, PER_KIND) ?? [])
        move.push({
          id: `company-${c.id}`,
          section: "go",
          icon: Building2,
          label: c.name,
          hint: "회사",
          run: go(() => void navigate({ to: "/brain/companies/$companyId", params: { companyId: c.id } })),
        });
      for (const r of (debounced === term ? references.data : undefined)?.slice(0, PER_KIND) ?? [])
        move.push({
          id: `ref-${r.id}`,
          section: "go",
          icon: Library,
          label: r.title,
          hint: ["참고 글", r.company_name].filter(Boolean).join(" · "),
          run: go(() => void navigate({ to: "/brain/references/$referenceId", params: { referenceId: r.id } })),
        });
      for (const d of take(shared.data, (d) => matches(term, d.title)))
        move.push({
          id: `doc-${d.id}`,
          section: "go",
          icon: Files,
          label: d.title,
          hint: "기본 문서",
          run: go(() => void navigate({ to: "/documents/$documentId", params: { documentId: d.id } })),
        });
    }
    // 빈 입력에서는 화면 목록이 할 일보다 아래로 (자주 쓰는 일 먼저)
    const moveShown = move.slice(0, term ? MOVE_CAP : PAGES.length);

    // ---- 할 일
    const actions: (Option & { words: string })[] = [
      {
        id: "do-posting",
        section: "do",
        icon: Link2,
        label: "공고 추가",
        hint: "주소 붙여넣기",
        words: "공고 추가 url 링크 채용 저장",
        run: go(() => {
          void navigate({ to: "/postings" });
          focusWhenReady("#posting-urls, [data-focus='posting-url']");
        }),
      },
      {
        id: "do-ingest",
        section: "do",
        icon: FolderInput,
        label: "브레인에 넣기",
        hint: "파일·붙여넣기",
        words: "자료 넣기 올리기 업로드 파일 이력서",
        // 팔레트가 닫힌 다음 시트를 연다 (초점이 시트로 가게)
        run: go(() => requestAnimationFrame(() => ingest.open())),
      },
      {
        id: "do-experience",
        section: "do",
        icon: Plus,
        label: "경험 추가",
        words: "경험 추가 새 경력 프로젝트",
        run: go(() => requestAnimationFrame(() => ingest.openExperience())),
      },
      {
        id: "do-today",
        section: "do",
        icon: CalendarClock,
        label: "오늘 마감 보기",
        words: "오늘 마감 일정 전형 시험",
        run: go(() => void navigate({ to: "/", search: { day: dateKey(new Date()) } })),
      },
    ];
    const doShown = actions.filter((a) => !term || matches(term, a.words));

    if (term) out.push(...moveShown, ...doShown);
    else out.push(...doShown, ...moveShown);

    // ---- 근거에서 찾기
    if (term) {
      const hits = debounced === term ? (evidence.data?.hits ?? []) : [];
      for (const h of hits.slice(0, EVIDENCE_CAP))
        out.push({
          id: `hit-${h.kind}-${h.id}`,
          section: "evidence",
          icon: h.kind === "block" ? FileText : h.record === "study" ? BookOpen : Quote,
          label: <span className="line-clamp-2">{h.snippet}</span>,
          hint: [EVIDENCE_KIND[h.kind] ?? "근거", h.experience_title ?? h.study_title ?? h.source_title]
            .filter(Boolean)
            .join(" · "),
          run: go(() => openHit(h, term)),
        });
      out.push({
        id: "hit-all",
        section: "evidence",
        icon: Search,
        label: (
          <>
            근거 전체 검색 <span className="text-muted">“{term}”</span>
          </>
        ),
        hint: "→",
        run: go(() => void navigate({ to: "/search", search: { q: term } })),
      });
    }
    return out;

    function openHit(h: SearchHit, query: string) {
      if (h.experience_id) void navigate({ to: "/brain/$experienceId", params: { experienceId: h.experience_id } });
      else if (h.study_item_id) void navigate({ to: "/study/items/$itemId", params: { itemId: h.study_item_id } });
      else void navigate({ to: "/search", search: { q: query } });
    }
  }, [
    term,
    debounced,
    postings.data,
    applications.data,
    shared.data,
    experiences.data,
    companies.data,
    references.data,
    evidence.data,
    navigate,
    ingest,
    close,
  ]);

  const [active, setActive] = useState(0);
  useEffect(() => setActive(0), [term]);
  const current = Math.min(active, options.length - 1);
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-index="${current}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!options.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + options.length) % options.length);
    } else if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      options[current]?.run();
    }
  };

  const sections = (["go", "do", "evidence"] as SectionKey[])
    .map((key) => ({ key, rows: options.map((o, i) => ({ o, i })).filter(({ o }) => o.section === key) }))
    .filter((s) => s.rows.length)
    // 화면에 보이는 순서 = 키보드 순서
    .sort((a, b) => a.rows[0]!.i - b.rows[0]!.i);
  const optionId = (i: number) => `${listId}-opt-${i}`;

  return (
    <>
      <div className="flex items-center gap-2 border-b border-line px-3">
        <Search size={17} className="shrink-0 text-muted" aria-hidden />
        <input
          autoFocus
          role="combobox"
          aria-expanded
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={options.length ? optionId(current) : undefined}
          aria-label="검색어 또는 할 일"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="공고·경험·회사 이름, 할 일, 근거 검색"
          maxLength={200}
          // 창 전체가 초점 자리라 입력 칸 테두리는 그리지 않는다 (전역 :focus-visible보다 앞서게 style로)
          style={{ outline: "none" }}
          className="h-12 min-w-0 flex-1 bg-transparent text-[15px] text-fg placeholder:text-muted"
        />
        {busy ? <Spinner className="shrink-0" /> : null}
        <kbd className="hidden shrink-0 rounded border border-line px-1.5 py-0.5 font-sans text-[11px] text-muted sm:inline">
          Esc
        </kbd>
        <CommandDialogClose
          aria-label="닫기"
          className="-mr-1 inline-flex size-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-fg sm:hidden"
        >
          <X size={18} />
        </CommandDialogClose>
      </div>
      <div ref={listRef} id={listId} role="listbox" aria-label="결과" className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2">
        {sections.map((s) => (
          <div key={s.key} role="group" aria-labelledby={`${listId}-${s.key}`} className="pb-1 not-first:pt-2">
            <div id={`${listId}-${s.key}`} className="px-2 pb-1 text-xs font-medium text-muted">
              {SECTION_LABEL[s.key]}
            </div>
            {s.rows.map(({ o, i }) => {
              const Icon = o.icon;
              return (
                <div
                  key={o.id}
                  id={optionId(i)}
                  data-index={i}
                  role="option"
                  aria-selected={i === current}
                  onMouseMove={() => i !== current && setActive(i)}
                  onClick={() => o.run()}
                  className={cn(
                    "flex min-h-10 cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm",
                    i === current ? "bg-subtle text-fg" : "text-fg",
                  )}
                >
                  <Icon size={16} className="shrink-0 text-muted" aria-hidden />
                  <span className="min-w-0 flex-1 break-words">{o.label}</span>
                  {o.hint ? (
                    <span className="max-w-[45%] shrink-0 truncate text-[13px] text-muted">{o.hint}</span>
                  ) : null}
                </div>
              );
            })}
          </div>
        ))}
        {term && evidence.isError ? (
          <p className="px-2 py-2 text-[13px] text-muted">근거 검색을 지금 쓸 수 없습니다. 근거 전체 검색에서 다시 해 보세요.</p>
        ) : null}
      </div>
      <div className="hidden items-center gap-4 border-t border-line px-4 py-2 text-xs text-muted sm:flex">
        <span>↑↓ 옮기기</span>
        <span>Enter 열기</span>
        <span>Esc 닫기</span>
      </div>
    </>
  );
}
