import { Badge } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/dates";
import { ASSESSMENT_KIND } from "../../lib/labels";

/** 오늘 화면: 일정이 잡힌 시험 준비의 D-day (공간 표시 시간대의 날짜 기준, 30일 안). 없으면 그리지 않는다. */
export function UpcomingTests({ max = 3 }: { max?: number }) {
  const q = useQuery({
    queryKey: ["upcoming-assessments"],
    queryFn: () => api.upcomingAssessments({ days: 30 }),
  });
  const rows = q.data ?? [];
  if (!rows.length) return null;
  return (
    <ul className="mb-2 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
      {rows.slice(0, max).map((a) => (
        <li key={a.id}>
          <Link
            to="/study"
            search={{ tab: "assessments" }}
            className="flex min-h-12 items-center gap-3 px-3 py-2.5 hover:bg-subtle/60"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{a.title}</div>
              <div className="truncate text-xs text-muted">
                {[ASSESSMENT_KIND[a.kind], a.requirement?.posting_title, formatDateTime(a.scheduled_at)]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
            <Badge tone={a.d_day <= 3 ? "danger" : a.d_day <= 7 ? "warn" : "accent"}>
              {a.d_day === 0 ? "D-day" : `D-${a.d_day}`}
            </Badge>
            <ChevronRight size={16} className="shrink-0 text-muted" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
