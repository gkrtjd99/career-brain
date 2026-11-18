import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api } from "../../lib/api";
import { notOrganizedNote, organizeSummary, type IngestItem } from "../../lib/ingest";

const LINK = "inline-flex min-h-9 shrink-0 items-center text-[13px] font-medium underline underline-offset-2 sm:min-h-8";

/**
 * 넣은 것 전체의 "정리 결과" 한 줄 (c0052). 자료마다 따로 보지 않고 여기서 확인할 것으로 간다.
 * 새 경험·합친 경험·확인할 사실은 이번에 넣은 것의 결과, 프로필·학력·기술 제안은 지금 남은 수다.
 */
export function OrganizeResult({ items, onNavigate }: { items: IngestItem[]; onNavigate: () => void }) {
  const sum = organizeSummary(items);
  const working = items.filter((x) => x.stage === "splitting").length;
  const caps = useQuery({ queryKey: ["ingest-capabilities"], queryFn: api.ingestCapabilities, staleTime: 60_000 });
  const status = useQuery({
    queryKey: ["organize"],
    queryFn: api.organizeStatus,
    enabled: sum.sources > 0,
  });
  const state = caps.data?.auto_extract ?? "on";
  const hasMine = items.some((x) => x.kind === "experience");

  if (!sum.sources && !working) {
    if (hasMine && state !== "on")
      return (
        <p className="flex flex-wrap items-center gap-x-2 text-[13px] text-muted">
          {notOrganizedNote(state)}.
          <Link
            to="/manage"
            search={{ tab: state === "off" ? "settings" : "models" }}
            onClick={onNavigate}
            className={LINK}
          >
            {state === "off" ? "설정" : "AI 연결"}
          </Link>
        </p>
      );
    return null;
  }

  const s = status.data;
  const parts = [
    sum.created ? `새 경험 ${sum.created}` : null,
    sum.matched ? `합친 경험 ${sum.matched}` : null,
    sum.claims ? `확인할 사실 ${sum.claims}` : null,
    s?.profile_values ? `프로필에 넣을 값 ${s.profile_values}` : null,
    s?.education ? `학력 제안 ${s.education}` : null,
    s?.skills ? `기술 제안 ${s.skills}` : null,
    s?.credentials ? `자격 후보 ${s.credentials}` : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-line pt-3" role="status">
      <p className="min-w-0 text-sm tabular-nums">
        <span className="font-semibold">정리 결과</span>
        {working ? <span className="text-muted"> · 정리 중 {working}</span> : null}
        {parts.length ? <span> · {parts.join(" · ")}</span> : sum.sources ? <span className="text-muted"> · 새로 뽑은 것이 없습니다</span> : null}
      </p>
      {parts.length ? (
        <Link to="/brain" search={{ check: true }} onClick={onNavigate} className={LINK}>
          확인하기
        </Link>
      ) : null}
    </div>
  );
}
