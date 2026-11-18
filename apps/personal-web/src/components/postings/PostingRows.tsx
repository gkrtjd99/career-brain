import type { PostingSummary } from "@career-brain/client";
import { Checkbox, cn, Disclosure, Spinner } from "@career-brain/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { hostOf } from "../../lib/format";
import { FETCH_ERROR, FIELD_LABEL } from "../../lib/labels";
import { applicationLabel, datesLine, postingTitle, timingLabel, timingSoon } from "../../lib/postings";

const ROW = "-mx-2 flex min-h-14 items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150";

/** 확인 전 공고의 손댈 일 한 마디 (오른쪽 위) */
function draftState(p: PostingSummary): { label: ReactNode; strong: boolean } {
  switch (p.last_fetch_status) {
    case "pending":
      return {
        label: (
          <span className="inline-flex items-center gap-1.5">
            <Spinner className="h-3 w-3" /> 가져오는 중
          </span>
        ),
        strong: false,
      };
    case "failed":
      return { label: "가져오기 실패", strong: true };
    case "listing":
      return { label: "공고 고르기", strong: true };
    case "needs_confirmation":
      return { label: "비용 확인", strong: true };
    default:
      return { label: "확인 필요", strong: true };
  }
}

/**
 * 공고 한 줄. 왼쪽: 제목(없으면 호스트) / 회사 · 날짜. 오른쪽 한 줄: 지원 상태 · D-day(또는 접수 시작).
 * 선택 모드에서는 줄을 누르면 고른다.
 */
export function PostingRow({
  p,
  selecting,
  selected,
  onToggle,
}: {
  p: PostingSummary;
  selecting: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const title = postingTitle(p);
  const draft = p.status === "draft";
  const failed = p.last_fetch_status === "failed";
  const meta = [p.company, draft && !p.title ? null : datesLine(p), !p.company && p.title ? hostOf(p.canonical_url) : null]
    .filter(Boolean)
    .join(" · ");

  const state = draft ? draftState(p) : null;
  const right = (
    <div className="flex max-w-[45%] shrink-0 flex-col items-end text-right text-[13px] tabular-nums">
      {state ? (
        <span className={cn("whitespace-nowrap", state.strong ? "font-medium text-fg" : "text-muted")}>{state.label}</span>
      ) : (
        <span className={cn("max-w-full truncate", timingSoon(p) && p.lifecycle !== "closed" ? "font-semibold text-fg" : "text-muted")}>
          {[p.application ? applicationLabel(p.application) : null, p.lifecycle === "closed" ? null : timingLabel(p)]
            .filter(Boolean)
            .join(" · ") || "마감"}
        </span>
      )}
    </div>
  );

  const body = (
    <>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{title}</div>
        {meta ? <div className="truncate text-[13px] text-muted tabular-nums">{meta}</div> : null}
        {failed ? (
          <div className="mt-0.5 truncate text-[13px] text-danger">
            {FETCH_ERROR[p.last_fetch_error ?? ""] ?? "가져오지 못했습니다"}
          </div>
        ) : null}
        {p.pending_changes.length && p.status === "saved" ? (
          <div className="mt-0.5 truncate text-[13px]">
            바뀐 항목 확인: {p.pending_changes.map((f) => FIELD_LABEL[f as keyof typeof FIELD_LABEL] ?? f).join(", ")}
          </div>
        ) : null}
      </div>
      {right}
    </>
  );

  if (selecting)
    return (
      <li>
        <label className={cn(ROW, "cursor-pointer hover:bg-subtle/60", selected && "bg-subtle/60")}>
          <Checkbox
            checked={selected}
            onCheckedChange={onToggle}
            aria-label={`${title} 고르기`}
          />
          {body}
        </label>
      </li>
    );
  return (
    <li>
      <Link to="/postings/$postingId" params={{ postingId: p.id }} className={cn(ROW, "hover:bg-subtle/60")}>
        {body}
      </Link>
    </li>
  );
}

export function PostingRows({
  items,
  selecting,
  selected,
  onToggle,
}: {
  items: PostingSummary[];
  selecting: boolean;
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <ul className="divide-y divide-line border-y border-line">
      {items.map((p) => (
        <PostingRow
          key={p.id}
          p={p}
          selecting={selecting}
          selected={selected.has(p.id)}
          onToggle={() => onToggle(p.id)}
        />
      ))}
    </ul>
  );
}

/** 접혀 있는 시기 묶음 (마감 공고) */
export function SeasonGroup({
  label,
  count,
  defaultOpen,
  children,
}: {
  label: string;
  count: number;
  defaultOpen: boolean;
  children: ReactNode;
}) {
  return (
    <Disclosure
      title={
        <>
          {label}
          <span className="font-normal text-muted tabular-nums">{count}</span>
        </>
      }
      defaultOpen={defaultOpen}
      tone="row"
      className="border-b border-line"
      contentClassName="pt-0"
    >
      <div className="pb-3 [&>ul]:border-b-0">{children}</div>
    </Disclosure>
  );
}
