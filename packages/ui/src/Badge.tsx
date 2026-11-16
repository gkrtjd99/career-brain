import type { HTMLAttributes } from "react";
import { cn } from "./cn";

/** ok: 확인됨 · warn: 확인 필요 · none: 근거 없음/비어 있음 · danger: 이견·실패 · accent: 강조 · neutral */
export type BadgeTone = "ok" | "warn" | "none" | "danger" | "accent" | "neutral";

const TONE: Record<BadgeTone, string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  none: "bg-subtle text-muted",
  danger: "bg-danger-soft text-danger",
  accent: "bg-accent-soft text-accent",
  neutral: "border border-line text-muted",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs leading-4 font-medium whitespace-nowrap",
        TONE[tone],
        className,
      )}
      {...props}
    />
  );
}
