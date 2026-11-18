import type { OrganizeStatus } from "@career-brain/client";
import { cn, Sheet } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { api } from "../../lib/api";
import { notOrganizedNote } from "../../lib/ingest";
import { SourceInbox } from "../../pages/Sources";

/** 확인할 것 한 줄의 조각 (순서 = 급한 것부터). to = 확인 창에서 갈 곳 */
function pieces(s: OrganizeStatus) {
  return [
    { n: s.needs_purpose, label: "무엇인지 확인", to: null },
    { n: s.failed, label: "실패", to: null },
    { n: s.pending_claims, label: "확인할 사실", to: "/brain/experiences", search: { review: true } },
    { n: s.duplicates, label: "같은 경험일 수 있음", to: "/brain/experiences", search: { dup: true } },
    { n: s.profile_values, label: "프로필에 넣을 값", to: "/brain/profile" },
    { n: s.education, label: "학력 제안", to: "/brain/education" },
    { n: s.skills, label: "기술 제안", to: "/brain/skills" },
    { n: s.credentials, label: "자격 후보", to: "/brain/credentials" },
    { n: s.expiring, label: "곧 만료", to: "/brain/credentials" },
  ].filter((p) => p.n > 0);
}

export function useOrganize() {
  return useQuery({
    queryKey: ["organize"],
    queryFn: api.organizeStatus,
    // 정리 중이면 조금씩 다시 본다
    refetchInterval: (q) => (q.state.data && q.state.data.processing > 0 ? 4000 : false),
  });
}

/**
 * 브레인 맨 위 한 줄: "정리 중 2 · 확인할 사실 12 · 프로필에 넣을 값 4". 누르면 확인할 것 창이 열린다.
 * 넣은 자료는 서버가 알아서 정리한다 (c0052). 정리하지 못하는 이유(AI 연결 없음·설정 끔)도 이 줄에서만 말한다.
 */
export function OrganizeLine({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const q = useOrganize();
  const s = q.data;
  if (!s) return <div className="h-11" aria-hidden />;
  const list = pieces(s);
  const text = [s.processing ? `정리 중 ${s.processing}` : null, ...list.map((p) => `${p.label} ${p.n}`)]
    .filter(Boolean)
    .join(" · ");
  const r = s.recent;
  const blocked = s.auto_extract !== "on";
  return (
    <>
      {list.length ? (
        <button
          type="button"
          onClick={() => onOpenChange(true)}
          aria-haspopup="dialog"
          className={cn(
            "-mx-2 flex min-h-11 w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-2 text-left",
            "transition-colors duration-150 hover:bg-subtle/60",
          )}
        >
          <span className="min-w-0 flex-1 text-sm tabular-nums">
            <span className="font-semibold">확인할 것</span>
            <span className="text-muted"> · </span>
            {text}
          </span>
          <ChevronRight size={16} className="shrink-0 text-muted" aria-hidden />
        </button>
      ) : (
        <p className="flex min-h-11 items-center text-sm text-muted tabular-nums">
          {s.processing ? `정리 중 ${s.processing}` : "확인할 것이 없습니다"}
          {!s.processing && r.sources ? ` · 지난 하루 자료 ${r.sources}개를 정리했습니다` : ""}
        </p>
      )}
      {blocked ? (
        <p className="flex flex-wrap items-center gap-x-2 text-[13px] text-muted">
          {notOrganizedNote(s.auto_extract).replace("했습니다", "합니다")}.
          {s.auto_extract !== "no_permission" ? (
            <Link
              to="/manage"
              search={{ tab: s.auto_extract === "off" ? "settings" : "models" }}
              className="inline-flex min-h-9 items-center text-fg underline underline-offset-2 sm:min-h-7"
            >
              {s.auto_extract === "off" ? "설정" : "AI 연결"}
            </Link>
          ) : null}
        </p>
      ) : null}
      <Sheet open={open} onOpenChange={onOpenChange} title="확인할 것">
        {open ? <ReviewQueue s={s} onNavigate={() => onOpenChange(false)} /> : null}
      </Sheet>
    </>
  );
}

const ROW =
  "-mx-2 flex min-h-11 items-center gap-3 rounded-md px-2 py-2 transition-colors duration-150 hover:bg-subtle/60";

function ReviewQueue({ s, onNavigate }: { s: OrganizeStatus; onNavigate: () => void }) {
  const top = useQuery({
    queryKey: ["experiences", "review-top"],
    queryFn: () => api.experiences({ status: "needs_review", sort: "updated", limit: 5 }),
    enabled: s.pending_claims > 0,
  });
  const r = s.recent;
  const result = [
    r.experiences_created ? `새 경험 ${r.experiences_created}` : null,
    r.experiences_matched ? `합친 경험 ${r.experiences_matched}` : null,
    r.claims_created ? `확인할 사실 ${r.claims_created}` : null,
    r.claims_attached ? `근거 더함 ${r.claims_attached}` : null,
  ].filter(Boolean);
  const links = pieces(s).filter((p) => p.to);
  return (
    <div className="space-y-8">
      {r.sources ? (
        <p className="text-sm tabular-nums">
          <span className="font-semibold">지난 하루 정리</span>
          <span className="text-muted"> · 자료 {r.sources}개</span>
          {result.length ? ` · ${result.join(" · ")}` : null}
        </p>
      ) : null}

      {s.needs_purpose || s.failed ? (
        <SourceInbox only={["purpose_unconfirmed", "parse_failed", "extraction_failed"]} title="받은 자료" />
      ) : null}

      {s.pending_claims ? (
        <section aria-labelledby="rq-claims">
          <h3 id="rq-claims" className="text-[15px] font-semibold">
            확인할 사실 <span className="font-normal text-muted tabular-nums">{s.pending_claims}</span>
          </h3>
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {(top.data ?? []).map((e) => (
              <li key={e.id}>
                <Link
                  to="/brain/$experienceId"
                  params={{ experienceId: e.id }}
                  onClick={onNavigate}
                  className={ROW}
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{e.title}</span>
                  <span className="shrink-0 text-[13px] font-semibold tabular-nums">{e.needs_review}</span>
                  <ChevronRight size={16} className="shrink-0 text-muted" aria-hidden />
                </Link>
              </li>
            ))}
            {s.review_experiences > (top.data?.length ?? 0) ? (
              <li>
                <Link
                  to="/brain/experiences"
                  search={{ review: true }}
                  onClick={onNavigate}
                  className={cn(ROW, "text-[13px] text-muted hover:text-fg")}
                >
                  <span className="flex-1">경험 {s.review_experiences}개 모두 보기</span>
                  <ChevronRight size={16} aria-hidden />
                </Link>
              </li>
            ) : null}
          </ul>
        </section>
      ) : null}

      {links.filter((p) => p.label !== "확인할 사실").length ? (
        <section aria-labelledby="rq-more">
          <h3 id="rq-more" className="text-[15px] font-semibold">
            그 밖에 확인할 것
          </h3>
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {links
              .filter((p) => p.label !== "확인할 사실")
              .map((p) => (
                <li key={p.label}>
                  <Link to={p.to!} search={p.search} onClick={onNavigate} className={ROW}>
                    <span className="min-w-0 flex-1 text-sm">{p.label}</span>
                    <span className="shrink-0 text-[13px] font-semibold tabular-nums">{p.n}</span>
                    <ChevronRight size={16} className="shrink-0 text-muted" aria-hidden />
                  </Link>
                </li>
              ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
