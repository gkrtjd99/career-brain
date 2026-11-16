import { Badge, Section } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/dates";
import { ACTION_LABEL } from "../lib/systemLabels";
import { LiveProvider, useLiveConnected } from "./system/live";

// 실시간 알림(SSE)이 끊겼을 때만 다시 읽는 간격
const FALLBACK_POLL_MS = 30_000;

/**
 * 오늘 화면의 "승인 요청" 카드. 대기 중인 요청이 있을 때만 보인다. 승인 상태 알림(SSE ``/core/live``)을 받으면
 * 다시 읽고, 알림이 끊겼으면 30초마다 다시 읽는다.
 */
export function ApprovalRequestsCard({ className }: { className?: string }) {
  return (
    <LiveProvider>
      <PendingApprovals className={className} />
    </LiveProvider>
  );
}

function PendingApprovals({ className }: { className?: string }) {
  const live = useLiveConnected();
  const q = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.approvals({ status: "pending", limit: 20 }),
    refetchInterval: live ? false : FALLBACK_POLL_MS,
  });
  const list = q.data ?? [];
  if (!list.length) return null;
  return (
    <Section
      title="승인 요청"
      className={className}
      action={
        <Link to="/system" search={{ tab: "approvals" }} className="text-[13px] text-muted hover:text-fg">
          전체
        </Link>
      }
    >
      <ul className="divide-y divide-line rounded-lg border border-warn/40 bg-surface">
        {list.slice(0, 5).map((a) => {
          const title = typeof a.summary.title === "string" ? a.summary.title : null;
          return (
            <li key={a.id}>
              <Link
                to="/system"
                search={{ tab: "approvals", approval: a.id }}
                className="flex min-h-12 items-center gap-3 px-3 py-2.5 hover:bg-subtle/60"
              >
                <ShieldCheck size={17} className="shrink-0 text-warn" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">
                    {ACTION_LABEL[a.action] ?? a.action}
                    {title ? ` · ${title}` : ""}
                  </div>
                  <div className="truncate text-xs text-muted">
                    {a.requested_via === "agent" ? "에이전트 요청" : "직접 요청"} · {formatDateTime(a.expires_at)}까지
                  </div>
                </div>
                <Badge tone="warn">확인</Badge>
                <ChevronRight size={16} className="shrink-0 text-muted" />
              </Link>
            </li>
          );
        })}
      </ul>
      {list.length > 5 ? <p className="mt-2 text-xs text-muted">나머지 {list.length - 5}건은 시스템 화면에서</p> : null}
    </Section>
  );
}
