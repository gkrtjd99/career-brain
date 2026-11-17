import { cva } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "./cn";

/** ok: 확인됨 · warn: 확인 필요 · none: 근거 없음/비어 있음 · danger: 이견·실패 · accent: 강조 · neutral */
export type BadgeTone = "ok" | "warn" | "none" | "danger" | "accent" | "neutral";

// shadcn/ui badge 의 cva 구성. 흑백 테마: 색 대신 모양으로 나눈다. 강조 = 채움, 확인 필요 = 진한 테두리, 확인됨 = 옅은 채움.
export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs leading-4 font-medium whitespace-nowrap",
  {
    variants: {
      tone: {
        ok: "bg-ok-soft text-ok",
        warn: "border border-warn text-warn",
        none: "bg-subtle text-muted",
        danger: "bg-danger-soft text-danger",
        accent: "bg-accent text-accent-fg",
        neutral: "border border-line text-muted",
      } satisfies Record<BadgeTone, string>,
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ tone, className, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
