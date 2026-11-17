import { cn } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/dates";
import { ASSESSMENT_KIND } from "../../lib/labels";
import { Rows } from "./parts";

/** 오늘 화면: 일정이 잡힌 시험 준비의 D-day (공간 표시 시간대의 날짜 기준, 30일 안). 없으면 그리지 않는다. */
export function UpcomingTests({ max = 3 }: { max?: number }) {
  const q = useQuery({
    queryKey: ["upcoming-assessments"],
    queryFn: () => api.upcomingAssessments({ days: 30 }),
  });
  const rows = q.data ?? [];
  if (!rows.length) return null;
  return (
    <Rows className="mb-2">
      {rows.slice(0, max).map((a) => (
        <li key={a.id}>
          <Link
            to="/study"
            search={{ tab: "assessments" }}
            className="-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{a.title}</div>
              <div className="truncate text-[13px] text-muted tabular-nums">
                {[ASSESSMENT_KIND[a.kind], a.requirement?.posting_title, formatDateTime(a.scheduled_at)]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
            <span className={cn("shrink-0 text-[13px] tabular-nums", a.d_day <= 3 ? "font-semibold" : "text-muted")}>
              {a.d_day === 0 ? "D-day" : `D-${a.d_day}`}
            </span>
          </Link>
        </li>
      ))}
    </Rows>
  );
}
