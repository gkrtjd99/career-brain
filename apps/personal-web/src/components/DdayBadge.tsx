import { Badge } from "@career-brain/ui";
import { daysLeft, ddayLabel } from "../lib/dates";

/** 마감까지 남은 날. 3일 이내는 강조, 지난 마감은 흐리게. 마감이 없으면 그리지 않는다. */
export function DdayBadge({ deadline }: { deadline: string | null | undefined }) {
  const label = ddayLabel(deadline);
  if (!label) return null;
  const n = daysLeft(deadline);
  const tone = n === null ? "neutral" : n < 0 ? "none" : n <= 3 ? "danger" : n <= 7 ? "warn" : "accent";
  return <Badge tone={tone}>{label}</Badge>;
}
