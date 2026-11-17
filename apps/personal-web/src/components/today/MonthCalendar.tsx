import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "@career-brain/ui";
import { AGENDA_KIND, type AgendaEvent, type AgendaKind } from "../../lib/agenda";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** 종류는 색이 아니라 모양으로 구분한다 (흑백 테마): 마감 = 채움, 전형 = 테두리, 시험 = 점선 */
export const KIND_CHIP: Record<AgendaKind, string> = {
  deadline: "bg-fg text-bg",
  stage: "border border-fg text-fg",
  test: "border border-dashed border-muted text-muted",
};

export const KIND_DOT: Record<AgendaKind, string> = {
  deadline: "bg-fg",
  stage: "border border-fg",
  test: "border border-dashed border-muted",
};

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function monthGrid(year: number, month: number) {
  const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: (string | null)[] = Array.from({ length: first }, () => null);
  for (let d = 1; d <= days; d++) cells.push(`${year}-${pad(month + 1)}-${pad(d)}`);
  while (cells.length % 7) cells.push(null);
  return cells;
}

export function MonthCalendar({
  events,
  today,
  selected,
  onSelect,
}: {
  events: AgendaEvent[];
  today: string;
  selected: string | null;
  onSelect: (day: string | null) => void;
}) {
  const ty = Number(today.slice(0, 4));
  const tm = Number(today.slice(5, 7));
  const [view, setView] = useState({ y: ty, m: tm - 1 });
  const cells = useMemo(() => monthGrid(view.y, view.m), [view]);
  const byDay = useMemo(() => {
    const map = new Map<string, AgendaEvent[]>();
    for (const e of events) map.set(e.date, [...(map.get(e.date) ?? []), e]);
    return map;
  }, [events]);
  const move = (delta: number) =>
    setView(({ y, m }) => {
      const t = m + delta;
      return { y: y + Math.floor(t / 12), m: ((t % 12) + 12) % 12 };
    });
  const isThisMonth = view.y === ty && view.m === tm - 1;

  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="flex items-center gap-1 border-b border-line px-3 py-1.5 sm:py-2">
        <div className="text-sm font-semibold">
          {view.y}년 {view.m + 1}월
        </div>
        <div className="ml-auto flex items-center gap-1">
          {!isThisMonth ? (
            <button
              type="button"
              onClick={() => setView({ y: ty, m: tm - 1 })}
              className="rounded px-2 py-1 text-xs text-muted hover:bg-subtle hover:text-fg"
            >
              이번 달
            </button>
          ) : null}
          <button
            type="button"
            aria-label="이전 달"
            onClick={() => move(-1)}
            className="rounded p-1 text-muted hover:bg-subtle hover:text-fg"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            type="button"
            aria-label="다음 달"
            onClick={() => move(1)}
            className="rounded p-1 text-muted hover:bg-subtle hover:text-fg"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
      <div className="grid grid-cols-7 border-b border-line text-center text-[11px] text-muted">
        {WEEKDAYS.map((w, i) => (
          <div key={w} className={cn("py-1 sm:py-1.5", i === 0 && "text-danger/80")}>
            {w}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {cells.map((day, i) => {
          const list = day ? (byDay.get(day) ?? []) : [];
          const past = day ? day < today : false;
          const isToday = day === today;
          const isSelected = day !== null && day === selected;
          return (
            <div
              key={day ?? `blank-${i}`}
              className={cn(
                "min-h-10 border-line p-0.5 sm:min-h-20 sm:p-1",
                i % 7 !== 6 && "border-r",
                i < cells.length - 7 && "border-b",
                !day && "bg-subtle/40",
              )}
            >
              {day ? (
                <button
                  type="button"
                  onClick={() => onSelect(isSelected ? null : day)}
                  aria-pressed={isSelected}
                  aria-label={`${Number(day.slice(5, 7))}월 ${Number(day.slice(8))}일 일정 ${list.length}개`}
                  className={cn(
                    "flex h-full w-full flex-col items-stretch gap-0.5 rounded text-left",
                    isSelected ? "bg-subtle" : "hover:bg-subtle/60",
                  )}
                >
                  <span
                    className={cn(
                      "mx-auto inline-flex size-5 items-center justify-center rounded-full text-[11px] sm:mx-0 sm:size-6 sm:text-xs",
                      isToday ? "bg-fg font-semibold text-bg" : past ? "text-muted/70" : "text-fg",
                    )}
                  >
                    {Number(day.slice(8))}
                  </span>
                  {/* 좁은 화면: 점, 넓은 화면: 이름 칩 2개 + 나머지 수 */}
                  <span className="flex flex-wrap justify-center gap-0.5 sm:hidden">
                    {list.slice(0, 3).map((e) => (
                      <span key={e.key} className={cn("size-1.5 rounded-full", KIND_DOT[e.kind])} />
                    ))}
                  </span>
                  <span className="hidden flex-col gap-0.5 sm:flex">
                    {list.slice(0, 2).map((e) => (
                      <span
                        key={e.key}
                        title={`${AGENDA_KIND[e.kind]} · ${e.title}${e.sub ? ` · ${e.sub}` : ""}`}
                        className={cn(
                          "truncate rounded px-1 py-px text-[11px] leading-4",
                          KIND_CHIP[e.kind],
                          past && "opacity-50",
                        )}
                      >
                        {e.title}
                      </span>
                    ))}
                    {list.length > 2 ? (
                      <span className="px-1 text-[11px] text-muted">+{list.length - 2}</span>
                    ) : null}
                  </span>
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-3 border-t border-line px-3 py-1.5 text-[11px] text-muted sm:py-2">
        {(Object.keys(AGENDA_KIND) as AgendaKind[]).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className={cn("inline-block h-2.5 w-4 rounded-sm", KIND_DOT[k])} />
            {AGENDA_KIND[k]}
          </span>
        ))}
        <Link to="/postings" className="ml-auto hidden hover:text-fg sm:inline">
          공고 마감은 저장한 공고에서
        </Link>
      </div>
    </div>
  );
}
