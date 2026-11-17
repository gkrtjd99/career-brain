import { cn } from "@career-brain/ui";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgendaEvent, AgendaKind } from "../../lib/agenda";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** 종류는 색 대신 작은 표시의 모양으로: 마감 = 채운 점, 전형 = 빈 점, 시험 = 점선 점, 만료 = 빈 네모 */
export function KindMark({ kind, className }: { kind: AgendaKind; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-1.5 shrink-0",
        kind === "expiry" ? "border border-fg" : "rounded-full",
        kind === "deadline" && "bg-fg",
        kind === "stage" && "border border-fg",
        kind === "test" && "border border-dashed border-muted",
        className,
      )}
    />
  );
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function keyOf(d: Date) {
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function fromKey(k: string) {
  return new Date(Date.UTC(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, Number(k.slice(8, 10))));
}

function monthGrid(year: number, month: number) {
  const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: (string | null)[] = Array.from({ length: first }, () => null);
  for (let d = 1; d <= days; d++) cells.push(`${year}-${pad(month + 1)}-${pad(d)}`);
  while (cells.length % 7) cells.push(null);
  return cells;
}

function weekOf(day: string) {
  const start = fromKey(day);
  start.setUTCDate(start.getUTCDate() - start.getUTCDay());
  return Array.from({ length: 7 }, (_, i) => {
    const x = new Date(start);
    x.setUTCDate(start.getUTCDate() + i);
    return keyOf(x);
  });
}

function useKindsByDay(events: AgendaEvent[]) {
  return useMemo(() => {
    const map = new Map<string, AgendaKind[]>();
    for (const e of events) {
      const kinds = map.get(e.date) ?? [];
      if (!kinds.includes(e.kind)) kinds.push(e.kind);
      map.set(e.date, kinds);
    }
    return map;
  }, [events]);
}

interface Props {
  events: AgendaEvent[];
  today: string;
  selected: string | null;
  onSelect: (day: string | null) => void;
}

function Header({
  label,
  onPrev,
  onNext,
  onToday,
}: {
  label: string;
  onPrev: () => void;
  onNext: () => void;
  onToday?: () => void;
}) {
  return (
    <div className="mb-2 flex items-center">
      <div className="text-sm font-semibold tabular-nums">{label}</div>
      <div className="ml-auto flex items-center">
        {onToday ? (
          <button
            type="button"
            onClick={onToday}
            className="inline-flex h-9 items-center rounded-md px-2 text-xs text-muted hover:bg-subtle/60 hover:text-fg md:h-7"
          >
            오늘로
          </button>
        ) : null}
        <button
          type="button"
          aria-label="이전"
          onClick={onPrev}
          className="inline-flex size-9 items-center justify-center rounded-md text-muted hover:bg-subtle/60 hover:text-fg md:size-7"
        >
          <ChevronLeft size={16} />
        </button>
        <button
          type="button"
          aria-label="다음"
          onClick={onNext}
          className="inline-flex size-9 items-center justify-center rounded-md text-muted hover:bg-subtle/60 hover:text-fg md:size-7"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

function DayButton({
  day,
  today,
  selected,
  kinds,
  onSelect,
  size,
}: {
  day: string;
  today: string;
  selected: string | null;
  kinds: AgendaKind[];
  onSelect: (day: string | null) => void;
  size: "sm" | "md";
}) {
  const isToday = day === today;
  const isSelected = day === selected;
  return (
    <button
      type="button"
      onClick={() => onSelect(isSelected ? null : day)}
      aria-pressed={isSelected}
      aria-label={`${Number(day.slice(5, 7))}월 ${Number(day.slice(8))}일${kinds.length ? ", 일정 있음" : ""}`}
      className={cn(
        "flex w-full flex-col items-center gap-1 rounded-md py-1 transition-colors duration-150",
        size === "md" && "min-h-11",
        isSelected ? "bg-subtle" : "hover:bg-subtle/60",
      )}
    >
      <span
        className={cn(
          "inline-flex items-center justify-center rounded-full tabular-nums",
          size === "sm" ? "size-7 text-[13px]" : "size-8 text-sm",
          isToday && "bg-fg font-semibold text-bg",
          !isToday && day < today && "text-muted",
        )}
      >
        {Number(day.slice(8))}
      </span>
      <span className="flex h-1.5 items-center gap-0.5">
        {kinds.map((k) => (
          <KindMark key={k} kind={k} />
        ))}
      </span>
    </button>
  );
}

/** 넓은 화면 옆자리용 작은 월 달력: 날짜와 일정 점만. 이름은 옆 목록이 보여 준다. */
export function MiniMonth({ events, today, selected, onSelect }: Props) {
  const ty = Number(today.slice(0, 4));
  const tm = Number(today.slice(5, 7)) - 1;
  const [view, setView] = useState({ y: ty, m: tm });
  const cells = useMemo(() => monthGrid(view.y, view.m), [view]);
  const kindsByDay = useKindsByDay(events);
  const move = (delta: number) =>
    setView(({ y, m }) => {
      const t = m + delta;
      return { y: y + Math.floor(t / 12), m: ((t % 12) + 12) % 12 };
    });

  return (
    <div>
      <Header
        label={`${view.y}년 ${view.m + 1}월`}
        onPrev={() => move(-1)}
        onNext={() => move(1)}
        onToday={view.y !== ty || view.m !== tm ? () => setView({ y: ty, m: tm }) : undefined}
      />
      <div className="grid grid-cols-7 text-center text-[11px] text-muted">
        {WEEKDAYS.map((w) => (
          <div key={w} className="pb-1">
            {w}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {cells.map((day, i) =>
          day ? (
            <DayButton
              key={day}
              day={day}
              today={today}
              selected={selected}
              kinds={kindsByDay.get(day) ?? []}
              onSelect={onSelect}
              size="sm"
            />
          ) : (
            <span key={`blank-${i}`} />
          ),
        )}
      </div>
    </div>
  );
}

/** 좁은 화면용 한 주 띠 */
export function WeekStrip({ events, today, selected, onSelect }: Props) {
  const [anchor, setAnchor] = useState(today);
  const days = useMemo(() => weekOf(anchor), [anchor]);
  const kindsByDay = useKindsByDay(events);
  const shift = (n: number) => {
    const d = fromKey(anchor);
    d.setUTCDate(d.getUTCDate() + n);
    setAnchor(keyOf(d));
  };
  const first = days[0]!;
  return (
    <div>
      <Header
        label={`${Number(first.slice(0, 4))}년 ${Number(first.slice(5, 7))}월`}
        onPrev={() => shift(-7)}
        onNext={() => shift(7)}
        onToday={!days.includes(today) ? () => setAnchor(today) : undefined}
      />
      <div className="grid grid-cols-7 text-center">
        {days.map((day, i) => (
          <div key={day}>
            <div className="pb-0.5 text-[11px] text-muted">{WEEKDAYS[i]}</div>
            <DayButton
              day={day}
              today={today}
              selected={selected}
              kinds={kindsByDay.get(day) ?? []}
              onSelect={onSelect}
              size="md"
            />
          </div>
        ))}
      </div>
    </div>
  );
}
