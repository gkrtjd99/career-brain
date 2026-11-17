import { useRef, type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "./cn";

/*
 * 목록 거르기와 보기 바꾸기의 두 모양 (plan §13):
 * - FilterChips / Chip: 목록을 거르는 회색 알약. 고른 것만 검정으로 채운다.
 * - ViewTabs: 같은 자료를 다른 보기로 바꾸는 밑줄 탭.
 */

const CHIP =
  "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-3.5 text-[13px] whitespace-nowrap tabular-nums " +
  "transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const CHIP_ON = "bg-fg font-medium text-bg";
const CHIP_OFF = "bg-subtle text-fg hover:bg-line";

export interface ChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** 켜짐 (aria-pressed) */
  pressed: boolean;
  children: ReactNode;
  count?: number | null;
}

/** 혼자 켜고 끄는 거름 알약 (예: "확인 필요만", 태그). */
export function Chip({ pressed, count, children, className, type = "button", ...props }: ChipProps) {
  return (
    <button type={type} aria-pressed={pressed} className={cn(CHIP, pressed ? CHIP_ON : CHIP_OFF, className)} {...props}>
      {children}
      {count !== undefined && count !== null ? <ChipCount on={pressed}>{count}</ChipCount> : null}
    </button>
  );
}

function ChipCount({ on, children }: { on: boolean; children: ReactNode }) {
  return <span className={on ? "text-bg/70" : "text-muted"}>{children}</span>;
}

export interface FilterChipItem<T> {
  value: T;
  label: ReactNode;
  count?: number | null;
  /** 좁은 화면에서 숨긴다 (고른 것은 늘 보인다) */
  hideOnMobile?: boolean;
}

export interface FilterChipsProps<T> {
  /** 무리 이름 (화면 읽기 프로그램용) */
  label: string;
  value: T;
  onChange: (value: T) => void;
  items: FilterChipItem<T>[];
  className?: string;
}

/**
 * 하나만 고르는 거름 (role=radiogroup). 화살표 키로 옮기며 고르고, Tab은 고른 칸 하나에만 멈춘다.
 * value는 undefined("전체")도 된다.
 */
export function FilterChips<T>({ label, value, onChange, items, className }: FilterChipsProps<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const selected = Math.max(
    0,
    items.findIndex((it) => it.value === value),
  );
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    const edge = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : null;
    if (!step && edge === null) return;
    e.preventDefault();
    const next = edge ?? (selected + step + items.length) % items.length;
    onChange(items[next]!.value);
    ref.current?.querySelectorAll<HTMLButtonElement>("[role=radio]")[next]?.focus();
  };
  return (
    <div
      ref={ref}
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn("flex flex-wrap items-center gap-1.5", className)}
    >
      {items.map((it, i) => {
        const on = i === selected && items[selected]!.value === value;
        return (
          <button
            key={String(it.value ?? "__all")}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={i === selected ? 0 : -1}
            onClick={() => onChange(it.value)}
            className={cn(CHIP, on ? CHIP_ON : CHIP_OFF, it.hideOnMobile && !on && "max-sm:hidden")}
          >
            {it.label}
            {it.count !== undefined && it.count !== null ? <ChipCount on={on}>{it.count}</ChipCount> : null}
          </button>
        );
      })}
    </div>
  );
}

export interface ViewTabsProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  items: { value: T; label: ReactNode }[];
  /** 탭 무리 이름 */
  label?: string;
  className?: string;
}

/** 보기 바꾸기 밑줄 탭 (role=tablist). 화살표 키로 옮긴다. 좁은 화면에서는 가로로 밀어 본다. */
export function ViewTabs<T extends string>({ value, onChange, items, label, className }: ViewTabsProps<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const selected = Math.max(
    0,
    items.findIndex((it) => it.value === value),
  );
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const edge = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : null;
    if (!step && edge === null) return;
    e.preventDefault();
    const next = edge ?? (selected + step + items.length) % items.length;
    onChange(items[next]!.value);
    ref.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
  };
  return (
    <div
      ref={ref}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn(
        "flex gap-0.5 overflow-x-auto overflow-y-hidden border-b border-line [scrollbar-width:none] sm:gap-1",
        className,
      )}
    >
      {items.map((it) => {
        const on = it.value === value;
        return (
          <button
            key={it.value}
            type="button"
            role="tab"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(it.value)}
            className={cn(
              "-mb-px inline-flex h-10 shrink-0 items-center border-b-2 px-2 text-sm whitespace-nowrap tabular-nums sm:px-3",
              "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
              on ? "border-fg font-medium text-fg" : "border-transparent text-muted transition-colors duration-150 hover:text-fg",
            )}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}
