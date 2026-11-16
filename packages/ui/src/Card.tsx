import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-line bg-surface", className)} {...props} />;
}

export interface SectionProps {
  title: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** 제목 + 내용. 숫자 카드 대신 목록을 담는 기본 묶음. */
export function Section({ title, action, children, className }: SectionProps) {
  return (
    <section className={cn("space-y-2", className)}>
      <div className="flex min-h-8 items-center justify-between gap-2">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function EmptyState({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="불러오는 중"
      className={cn(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent",
        className,
      )}
    />
  );
}
