import type { IngestKind } from "@career-brain/client";
import { Button, cn, Input, Spinner, Tooltip, TooltipContent, TooltipTrigger } from "@career-brain/ui";
import { Link } from "@tanstack/react-router";
import { X } from "lucide-react";
import { useState } from "react";
import {
  choose,
  FILE_KINDS,
  KIND_LABEL,
  reasonText,
  removeItem,
  URL_KINDS,
  type IngestItem,
} from "../../lib/ingest";

const STAGE_LABEL: Record<IngestItem["stage"], string> = {
  waiting: "기다리는 중",
  reading: "읽는 중",
  saving: "보내는 중",
  splitting: "정리 중",
  saved: "저장됨",
  confirm: "확인 필요",
  failed: "실패",
};

const READ_LABEL = { device: "이 기기에서 읽음", model: "연결된 모델로 읽음", server: "서버에서 읽음" } as const;

const CHIP =
  "inline-flex h-9 items-center rounded-full px-3 text-[13px] transition-colors duration-150 sm:h-8 " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50";

/** 넣은 것 목록. 줄마다 무엇으로 보는지와 한 번에 바꾸는 칩, 진행 상태 한 줄. */
export function IngestQueue({ items, onNavigate }: { items: IngestItem[]; onNavigate: () => void }) {
  return (
    <ul className="divide-y divide-line border-y border-line">
      {items.map((it) => (
        <Row key={it.id} it={it} onNavigate={onNavigate} />
      ))}
    </ul>
  );
}

function Row({ it, onNavigate }: { it: IngestItem; onNavigate: () => void }) {
  const isUrl = it.input.type === "url";
  const kinds: IngestKind[] = isUrl ? URL_KINDS : FILE_KINDS;
  const busy = ["waiting", "reading", "saving", "splitting"].includes(it.stage);
  // 주소는 보낸 뒤에 바꿀 수 없다 (공고·회사 페이지는 다른 곳에 저장된다)
  const locked = isUrl ? it.stage === "saved" || it.stage === "saving" : it.stage === "saving";
  const needsName = isUrl && it.kind === "company" && !it.companyId && it.stage === "confirm";
  const why = it.stage === "confirm" && !needsName ? reasonText(it.reason, it.suggested) : null;

  return (
    <li className="space-y-2 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{it.name}</div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[13px] tabular-nums">
            <span
              className={cn(
                it.stage === "failed" && "font-medium text-danger",
                it.stage === "confirm" && "font-semibold",
                it.stage === "saved" && "font-medium",
                busy && "text-muted",
              )}
            >
              {STAGE_LABEL[it.stage]}
              {it.stage === "reading" && it.progress !== null ? ` ${Math.round(it.progress * 100)}%` : ""}
            </span>
            {busy ? <Spinner /> : null}
            {it.readBy && it.input.type === "file" ? <span className="text-muted">· {READ_LABEL[it.readBy]}</span> : null}
            {it.note ? <span className="text-muted">· {it.note}</span> : null}
          </p>
          {why ? <p className="text-[13px] text-muted">{why}. 맞으면 확인을 누르세요.</p> : null}
        </div>
        <Destination it={it} onNavigate={onNavigate} />
        {!busy ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => removeItem(it.id)}
                className="-mr-1.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-fg sm:size-8"
                aria-label={`${it.name} 목록에서 빼기`}
              >
                <X size={15} />
              </button>
            </TooltipTrigger>
            <TooltipContent>목록에서 빼기</TooltipContent>
          </Tooltip>
        ) : null}
      </div>

      {it.stage !== "failed" ? (
        <div className="-ml-1 flex flex-wrap items-center gap-1" role="radiogroup" aria-label={`${it.name} 무엇으로 넣을까요`}>
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={it.kind === k}
              disabled={locked}
              onClick={() => (it.kind === k && it.stage !== "confirm" ? undefined : choose(it.id, k))}
              className={cn(CHIP, it.kind === k ? "bg-fg font-medium text-bg" : "text-muted hover:bg-subtle hover:text-fg")}
            >
              {KIND_LABEL[k]}
            </button>
          ))}
          {it.stage === "confirm" && it.kind && !needsName ? (
            <Button size="sm" variant="primary" className="ml-1" onClick={() => choose(it.id, it.kind!)}>
              확인
            </Button>
          ) : null}
        </div>
      ) : null}

      {needsName ? <CompanyName it={it} /> : null}
    </li>
  );
}

/** 회사 페이지인데 이 공간의 회사와 이어지지 않음: 이름을 받아 회사를 만들고 가져온다 */
function CompanyName({ it }: { it: IngestItem }) {
  const guess = it.companyHint ? it.companyHint.charAt(0).toUpperCase() + it.companyHint.slice(1) : "";
  const [name, setName] = useState(guess);
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) choose(it.id, "company", name);
      }}
    >
      <div className="min-w-0 flex-1">
        <label htmlFor={`co-${it.id}`} className="mb-1 block text-[13px] text-muted">
          어느 회사의 페이지인가요? 같은 이름의 회사가 없어 새로 만듭니다.
        </label>
        <Input id={`co-${it.id}`} value={name} onChange={(e) => setName(e.target.value)} placeholder="회사 이름" />
      </div>
      <Button type="submit" variant="primary" disabled={!name.trim()}>
        만들고 가져오기
      </Button>
    </form>
  );
}

const LINK = "inline-flex min-h-9 shrink-0 items-center text-[13px] font-medium underline underline-offset-2 sm:min-h-8";

function Destination({ it, onNavigate }: { it: IngestItem; onNavigate: () => void }) {
  if (it.stage !== "saved" && it.stage !== "confirm" && it.stage !== "failed") return null;
  if (it.postingId)
    return (
      <Link to="/postings/$postingId" params={{ postingId: it.postingId }} onClick={onNavigate} className={LINK}>
        공고 보기
      </Link>
    );
  if (it.companyId && it.stage === "saved")
    return (
      <Link to="/brain/companies/$companyId" params={{ companyId: it.companyId }} onClick={onNavigate} className={LINK}>
        회사 보기
      </Link>
    );
  if (it.sourceId && it.kind === "reference" && it.stage === "saved")
    return (
      <Link to="/brain/references" search={{ from: it.sourceId }} onClick={onNavigate} className={LINK}>
        참고 글로 만들기
      </Link>
    );
  if (it.sourceId && it.kind === "experience" && it.stage === "saved" && /사실 [1-9]/.test(it.note ?? ""))
    return (
      <Link to="/brain" search={{ check: true }} onClick={onNavigate} className={LINK}>
        확인하기
      </Link>
    );
  if (it.sourceId)
    return (
      <Link to="/brain/sources/$sourceId" params={{ sourceId: it.sourceId }} onClick={onNavigate} className={LINK}>
        자료 보기
      </Link>
    );
  return null;
}
