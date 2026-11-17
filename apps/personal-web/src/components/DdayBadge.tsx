import { cn } from "@career-brain/ui";
import { daysLeft, ddayLabel } from "../lib/dates";

/** 마감까지 남은 날. 배지 대신 글자로: 3일 안이면 진하게, 그 밖은 흐리게. 마감이 없으면 그리지 않는다. */
export function DdayBadge({ deadline }: { deadline: string | null | undefined }) {
  const label = ddayLabel(deadline);
  if (!label) return null;
  const n = daysLeft(deadline);
  const soon = n !== null && n >= 0 && n <= 3;
  return (
    <span className={cn("text-[13px] whitespace-nowrap tabular-nums", soon ? "font-semibold text-fg" : "text-muted")}>
      {label}
    </span>
  );
}
