import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "./Badge";
import { cn } from "./cn";

export type VerificationStatus =
  | "extracted"
  | "self_confirmed"
  | "evidence_supported"
  | "disputed"
  | "retired";

export const STATUS_LABEL: Record<VerificationStatus, string> = {
  extracted: "확인 필요",
  self_confirmed: "본인 확인",
  evidence_supported: "자료 확인",
  disputed: "이견 있음",
  retired: "보관됨",
};

const STATUS_TONE: Record<VerificationStatus, BadgeTone> = {
  extracted: "warn",
  self_confirmed: "ok",
  evidence_supported: "ok",
  disputed: "danger",
  retired: "none",
};

/** 색과 문구를 함께 쓰는 확인 상태 배지. */
export function StatusBadge({ status }: { status: VerificationStatus | string }) {
  const known = status in STATUS_LABEL ? (status as VerificationStatus) : null;
  return (
    <Badge tone={known ? STATUS_TONE[known] : "neutral"} data-status={status}>
      {known ? STATUS_LABEL[known] : status}
    </Badge>
  );
}

export interface EvidenceCardProps {
  title: ReactNode;
  status: VerificationStatus;
  sourceLabel?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function EvidenceCard({
  title,
  status,
  sourceLabel,
  children,
  actions,
  className,
}: EvidenceCardProps) {
  return (
    <article
      data-status={status}
      className={cn("rounded-lg border border-line bg-surface p-3", className)}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 text-sm leading-6">{title}</div>
        <StatusBadge status={status} />
      </div>
      {sourceLabel ? <div className="mt-1 text-xs text-muted">{sourceLabel}</div> : null}
      {children}
      {actions ? <div className="mt-2 flex flex-wrap gap-1.5">{actions}</div> : null}
    </article>
  );
}
