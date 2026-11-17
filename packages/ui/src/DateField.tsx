import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ChangeEvent, type ReactNode } from "react";
import { cn } from "./cn";
import { FIELD } from "./Form";
import { Popover, PopoverContent, PopoverTrigger } from "./Popover";

/*
 * 한국 양식의 날짜 칸. 값은 API 그대로(YYYY-MM, YYYY-MM-DD, HH:MM)이고 화면에는 점으로 적는다.
 *
 * - MonthField: 기간(입학·졸업·입사·퇴사·입대…)은 달까지만 쓴다. "2024.03"을 치거나 달 고르기 창에서 고른다.
 * - DateField: "2024.03.15"를 치거나(숫자만 쳐도 점이 들어간다) 달력에서 고른다. 생년월일처럼 먼 과거는
 *   달력으로 수십 년을 넘기기보다 치는 쪽이 빨라서 `calendar={false}`로 달력 단추를 뺀다.
 * - TimeField: "18:00" 또는 "1800". 24시간제.
 *
 * 치는 동안 틀린 값은 위로 올리지 않는다. 칸을 벗어날 때도 틀리면 빨간 테두리와 한 줄 안내를 보인다.
 */

const pad = (n: number) => String(n).padStart(2, "0");

export function parseMonth(text: string): string | null {
  const m = /^\s*(\d{4})\s*[.\-/년]?\s*(\d{1,2})\s*[.월]?\s*$/.exec(text);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  if (y < 1900 || y > 2100 || mo < 1 || mo > 12) return null;
  return `${y}-${pad(mo)}`;
}

export function parseDate(text: string): string | null {
  const m = /^\s*(\d{4})\s*[.\-/년]?\s*(\d{1,2})\s*[.\-/월]?\s*(\d{1,2})\s*[.일]?\s*$/.exec(text);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (y < 1900 || y > 2100 || mo < 1 || mo > 12 || d < 1 || d > daysIn(y, mo - 1)) return null;
  return `${y}-${pad(mo)}-${pad(d)}`;
}

export function parseTime(text: string): string | null {
  const m = /^\s*(\d{1,2})\s*(?:[:시]\s*(\d{1,2})?\s*분?|(\d{2}))?\s*$/.exec(text);
  if (!m) return null;
  const h = Number(m[1]);
  const mi = Number(m[2] ?? m[3] ?? 0);
  if (h > 23 || mi > 59) return null;
  return `${pad(h)}:${pad(mi)}`;
}

function rangeHint(min: string, max: string) {
  if (min && max) return `${min}부터 ${max}까지 넣을 수 있습니다.`;
  return min ? `${min} 이후로 넣으세요.` : `${max} 이전으로 넣으세요.`;
}

const showMonth = (v: string) => (v ? v.slice(0, 7).replace("-", ".") : "");
const showDate = (v: string) => (v ? v.slice(0, 10).replaceAll("-", ".") : "");

function daysIn(y: number, m0: number) {
  return new Date(Date.UTC(y, m0 + 1, 0)).getUTCDate();
}

function todayParts() {
  const d = new Date();
  return { y: d.getFullYear(), m: d.getMonth(), d: d.getDate() };
}

/** 숫자를 치는 동안 점을 넣는다 (지울 때는 넣지 않는다). */
function autoDots(next: string, prev: string, withDay: boolean) {
  if (next.length <= prev.length) return next;
  if (/^\d{4}$/.test(next)) return `${next}.`;
  if (withDay && /^\d{4}\.\d{2}$/.test(next)) return `${next}.`;
  if (/^\d{6}$/.test(next)) return `${next.slice(0, 4)}.${next.slice(4)}${withDay ? "." : ""}`;
  if (withDay && /^\d{8}$/.test(next)) return `${next.slice(0, 4)}.${next.slice(4, 6)}.${next.slice(6)}`;
  return next;
}

interface TextFieldProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
}

/** 치는 칸 + 틀렸을 때 한 줄. format: 값→글, parse: 글→값(null = 틀림) */
function useTyped(value: string, onChange: (v: string) => void, format: (v: string) => string, parse: (t: string) => string | null) {
  const [draft, setDraft] = useState(() => format(value));
  const [bad, setBad] = useState(false);
  useEffect(() => {
    // 밖에서 값이 바뀌면(달력에서 고름·초기화) 글을 맞춘다. 치는 중 같은 값이면 그대로 둔다.
    setDraft((d) => (parse(d) === value || (!value && !d.trim()) ? d : format(value)));
    setBad(false);
  }, [value]);
  const take = (text: string) => {
    setDraft(text);
    if (!text.trim()) {
      setBad(false);
      if (value) onChange("");
      return;
    }
    const v = parse(text);
    if (v) {
      setBad(false);
      if (v !== value) onChange(v);
    }
  };
  const blur = () => {
    if (!draft.trim()) return setBad(false);
    const v = parse(draft);
    if (v) setDraft(format(v));
    setBad(!v);
  };
  return { draft, bad, take, blur, setDraft };
}

function FieldShell({
  bad,
  hint,
  errorId,
  children,
  className,
}: {
  bad: boolean;
  hint: string;
  errorId: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("w-full", className)}>
      <div className="relative">{children}</div>
      {bad ? (
        <p id={errorId} className="mt-1 text-xs text-danger">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

const PICK_BUTTON =
  "absolute top-1/2 right-1 grid size-7 -translate-y-1/2 place-items-center rounded-sm text-muted hover:bg-subtle hover:text-fg " +
  "focus-visible:outline-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-50";

export interface MonthFieldProps extends TextFieldProps {
  /** YYYY-MM: 이보다 앞 달은 고르지 못한다 */
  min?: string;
  max?: string;
}

/** 달 칸: "2024.03". 값은 YYYY-MM. */
export function MonthField({ id, value, onChange, disabled, className, min, max, ...aria }: MonthFieldProps) {
  const errorId = useId();
  const t = useTyped(value, onChange, showMonth, (s) => {
    const v = parseMonth(s);
    return v && (!min || v >= min) && (!max || v <= max) ? v : null;
  });
  const [open, setOpen] = useState(false);
  return (
    <FieldShell
      bad={t.bad}
      hint={parseMonth(t.draft) ? rangeHint(showMonth(min ?? ""), showMonth(max ?? "")) : "2024.03 처럼 넣으세요."}
      errorId={errorId}
      className={className}
    >
      <input
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder="YYYY.MM"
        maxLength={10}
        value={t.draft}
        disabled={disabled}
        aria-invalid={t.bad || undefined}
        aria-describedby={cn(t.bad && errorId, aria["aria-describedby"]) || undefined}
        aria-label={aria["aria-label"]}
        onChange={(e: ChangeEvent<HTMLInputElement>) => t.take(autoDots(e.target.value, t.draft, false))}
        onBlur={t.blur}
        className={cn(FIELD, "h-9 pr-9 tabular-nums")}
      />
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger className={PICK_BUTTON} disabled={disabled} aria-label="달 고르기">
          <CalendarDays size={15} aria-hidden />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-64 p-2">
          <MonthGrid
            value={value}
            min={min}
            max={max}
            onPick={(v) => {
              onChange(v);
              setOpen(false);
            }}
          />
        </PopoverContent>
      </Popover>
    </FieldShell>
  );
}

function NavHeader({ label, onPrev, onNext }: { label: string; onPrev: () => void; onNext: () => void }) {
  return (
    <div className="mb-1 flex items-center">
      <button type="button" onClick={onPrev} aria-label="이전" className={cn(NAV, "mr-auto")}>
        <ChevronLeft size={16} />
      </button>
      <div className="text-sm font-semibold tabular-nums" aria-live="polite">
        {label}
      </div>
      <button type="button" onClick={onNext} aria-label="다음" className={cn(NAV, "ml-auto")}>
        <ChevronRight size={16} />
      </button>
    </div>
  );
}

const NAV =
  "grid size-9 place-items-center rounded-md text-muted hover:bg-subtle hover:text-fg sm:size-8 focus-visible:outline-2 focus-visible:outline-accent";
const CELL =
  "grid place-items-center rounded-md text-[13px] tabular-nums transition-colors duration-150 hover:bg-subtle " +
  "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-30";
const CELL_ON = "bg-fg font-medium text-bg hover:bg-fg";

/** 연도 넘기기 + 12달 */
export function MonthGrid({ value, onPick, min, max }: { value: string; onPick: (v: string) => void; min?: string; max?: string }) {
  const now = todayParts();
  const [year, setYear] = useState(() => (value ? Number(value.slice(0, 4)) : now.y));
  const thisMonth = `${now.y}-${pad(now.m + 1)}`;
  return (
    <div>
      <NavHeader label={`${year}년`} onPrev={() => setYear(year - 1)} onNext={() => setYear(year + 1)} />
      <div className="grid grid-cols-4 gap-1">
        {Array.from({ length: 12 }, (_, i) => {
          const v = `${year}-${pad(i + 1)}`;
          const off = (!!min && v < min) || (!!max && v > max);
          return (
            <button
              key={v}
              type="button"
              disabled={off}
              aria-pressed={v === value}
              onClick={() => onPick(v)}
              className={cn(CELL, "h-10 sm:h-9", v === value ? CELL_ON : v === thisMonth && "underline underline-offset-4")}
            >
              {i + 1}월
            </button>
          );
        })}
      </div>
    </div>
  );
}

const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

/** 한 달 달력 (일요일부터). */
export function Calendar({ value, onPick, min, max }: { value: string; onPick: (v: string) => void; min?: string; max?: string }) {
  const now = todayParts();
  const [view, setView] = useState(() =>
    value ? { y: Number(value.slice(0, 4)), m: Number(value.slice(5, 7)) - 1 } : { y: now.y, m: now.m },
  );
  const today = `${now.y}-${pad(now.m + 1)}-${pad(now.d)}`;
  const cells = useMemo(() => {
    const first = new Date(Date.UTC(view.y, view.m, 1)).getUTCDay();
    const n = daysIn(view.y, view.m);
    const out: (string | null)[] = Array.from({ length: first }, () => null);
    for (let d = 1; d <= n; d++) out.push(`${view.y}-${pad(view.m + 1)}-${pad(d)}`);
    return out;
  }, [view]);
  const step = (k: number) => {
    const m = view.m + k;
    setView({ y: view.y + Math.floor(m / 12), m: ((m % 12) + 12) % 12 });
  };
  return (
    <div>
      <NavHeader label={`${view.y}년 ${view.m + 1}월`} onPrev={() => step(-1)} onNext={() => step(1)} />
      <div className="grid grid-cols-7 gap-0.5 text-center">
        {WEEK.map((w) => (
          <div key={w} className="py-1 text-xs text-muted" aria-hidden>
            {w}
          </div>
        ))}
        {cells.map((d, i) =>
          d ? (
            <button
              key={d}
              type="button"
              disabled={(!!min && d < min) || (!!max && d > max)}
              aria-pressed={d === value}
              aria-label={`${view.m + 1}월 ${Number(d.slice(8))}일`}
              onClick={() => onPick(d)}
              className={cn(CELL, "h-9 sm:h-8", d === value ? CELL_ON : d === today && "font-semibold underline underline-offset-4")}
            >
              {Number(d.slice(8))}
            </button>
          ) : (
            <span key={`e${i}`} />
          ),
        )}
      </div>
    </div>
  );
}

export interface DateFieldProps extends TextFieldProps {
  /** 달력 단추. 생년월일처럼 먼 과거는 끈다 */
  calendar?: boolean;
  min?: string;
  max?: string;
}

/** 날짜 칸: "2024.03.15". 값은 YYYY-MM-DD. */
export function DateField({ id, value, onChange, disabled, className, calendar = true, min, max, ...aria }: DateFieldProps) {
  const errorId = useId();
  const t = useTyped(value, onChange, showDate, (s) => {
    const v = parseDate(s);
    return v && (!min || v >= min) && (!max || v <= max) ? v : null;
  });
  const [open, setOpen] = useState(false);
  const outOfRange = !!parseDate(t.draft);
  return (
    <FieldShell
      bad={t.bad}
      hint={outOfRange ? rangeHint(showDate(min ?? ""), showDate(max ?? "")) : "2024.03.15 처럼 넣으세요."}
      errorId={errorId}
      className={className}
    >
      <input
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder="YYYY.MM.DD"
        maxLength={13}
        value={t.draft}
        disabled={disabled}
        aria-invalid={t.bad || undefined}
        aria-describedby={cn(t.bad && errorId, aria["aria-describedby"]) || undefined}
        aria-label={aria["aria-label"]}
        onChange={(e: ChangeEvent<HTMLInputElement>) => t.take(autoDots(e.target.value, t.draft, true))}
        onBlur={t.blur}
        className={cn(FIELD, "h-9 tabular-nums", calendar && "pr-9")}
      />
      {calendar ? (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger className={PICK_BUTTON} disabled={disabled} aria-label="달력에서 고르기">
            <CalendarDays size={15} aria-hidden />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 p-2">
            <Calendar
              value={value}
              min={min}
              max={max}
              onPick={(v) => {
                onChange(v);
                setOpen(false);
              }}
            />
          </PopoverContent>
        </Popover>
      ) : null}
    </FieldShell>
  );
}

/** 시각 칸: "18:00". 값은 HH:MM. */
export function TimeField({ id, value, onChange, disabled, className, ...aria }: TextFieldProps) {
  const errorId = useId();
  const t = useTyped(value, onChange, (v) => v, parseTime);
  return (
    <FieldShell bad={t.bad} hint="18:00 처럼 넣으세요." errorId={errorId} className={className}>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder="HH:MM"
        maxLength={5}
        value={t.draft}
        disabled={disabled}
        aria-invalid={t.bad || undefined}
        aria-describedby={cn(t.bad && errorId, aria["aria-describedby"]) || undefined}
        aria-label={aria["aria-label"]}
        onChange={(e) => {
          const next = e.target.value;
          t.take(next.length > t.draft.length && /^\d{2}$/.test(next) ? `${next}:` : next);
        }}
        onBlur={t.blur}
        className={cn(FIELD, "h-9 tabular-nums")}
      />
    </FieldShell>
  );
}

/** 날짜 + 시각 칸. 값은 datetime-local 과 같은 "YYYY-MM-DDTHH:MM" (시각을 비우면 00:00). */
export function DateTimeField({ id, value, onChange, disabled, className, ...aria }: TextFieldProps) {
  const date = value.slice(0, 10);
  const time = value.length > 11 ? value.slice(11, 16) : "";
  const label = aria["aria-label"];
  return (
    <div className={cn("flex gap-2", className)}>
      <DateField
        id={id}
        value={date}
        disabled={disabled}
        aria-label={label}
        onChange={(d) => onChange(d ? `${d}T${time || "00:00"}` : "")}
        className="min-w-0 flex-1"
      />
      <TimeField
        value={date ? time : ""}
        disabled={disabled || !date}
        aria-label={label ? `${label} 시각` : "시각"}
        onChange={(tm) => onChange(date ? `${date}T${tm || "00:00"}` : "")}
        className="w-24 shrink-0"
      />
    </div>
  );
}
