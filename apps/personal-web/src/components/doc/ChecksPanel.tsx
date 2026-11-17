import type { Finding, ValidateResult } from "@career-brain/client";
import { cn, EmptyState } from "@career-brain/ui";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { FINDING, FINDING_GROUP, type FindingGroup } from "../../lib/labels";
import { toneText, VisibilityTable } from "../../pages/Application";

const ORDER: FindingGroup[] = ["limit", "risk", "manipulation", "sensitive", "evidence", "info"];

function detailText(f: Finding): string | null {
  const d = f.detail ?? {};
  const pick = (k: string) => (Array.isArray(d[k]) ? (d[k] as unknown[]).map(String) : null);
  const values =
    pick("companies") ?? pick("numbers") ?? pick("terms") ?? pick("phrases") ?? pick("characters") ?? pick("words");
  if (values?.length) return values.join(", ");
  if (typeof d.term === "string") return `${d.term} ${String(d.count ?? "")}번`;
  if (typeof d.match === "string") return d.match;
  if (Array.isArray(d.kinds)) {
    return (d.kinds as string[]).map((k) => (k === "number" ? "수치" : k === "experience" ? "경험" : k)).join("·");
  }
  return null;
}

/** 저장한 revision 검사 결과: 위험 경고는 크게, 참고는 작게. 경고 목록일 뿐 판정이 아니다. */
export function ChecksPanel({
  result,
  dirty,
  onJump,
  blockNo,
}: {
  result: ValidateResult;
  dirty: boolean;
  onJump: (blockId: string) => void;
  blockNo: (blockId: string) => number | null;
}) {
  const groups = new Map<FindingGroup, Finding[]>();
  for (const f of result.findings) {
    const g = FINDING[f.code]?.group ?? "info";
    groups.set(g, [...(groups.get(g) ?? []), f]);
  }
  const danger = (groups.get("risk")?.length ?? 0) + (groups.get("manipulation")?.length ?? 0);
  return (
    <div className="space-y-6">
      <p className="text-xs text-muted">
        r{result.revision} 기준 · 코드 검사(모델 없음) · AI 수정 문단 {Math.round(result.ai_ratio * 100)}%
        {dirty ? " · 저장하지 않은 편집은 아직 검사하지 않았습니다." : ""}
      </p>
      {danger ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger-soft px-3 py-2.5 text-[13px] text-danger"
        >
          <ShieldAlert size={16} className="mt-0.5 shrink-0" />
          <span>
            제출 전에 꼭 볼 경고 {danger}개: 다른 회사 이름, 회사만 바꿔도 되는 문장, 숨김 문자·심사 지시문 같은 조작
            의심.
          </span>
        </div>
      ) : null}
      {!result.findings.length ? <EmptyState>경고가 없습니다.</EmptyState> : null}
      {ORDER.map((g) => {
        const list = groups.get(g);
        if (!list?.length) return null;
        const info = FINDING_GROUP[g];
        return (
          <section key={g}>
            <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold">
              {g !== "info" ? (
                <AlertTriangle size={14} className={g === "evidence" ? "text-muted" : "text-danger"} />
              ) : null}
              {info.label}
              <span className="font-normal text-muted tabular-nums">{list.length}</span>
            </h3>
            <ul className="divide-y divide-line border-t border-line">
              {list.map((f, i) => {
                const no = f.block_id ? blockNo(f.block_id) : null;
                const extra = detailText(f);
                return (
                  <li
                    key={`${f.code}-${i}`}
                    className={cn("py-2.5", g === "info" ? "text-xs text-muted" : "text-[13px]")}
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className={cn("font-medium", g === "info" ? "text-muted" : toneText(info.tone))}>
                        {FINDING[f.code]?.label ?? f.code}
                      </span>
                      {no !== null ? (
                        <button
                          type="button"
                          className="-my-1 rounded px-1 py-1 text-xs text-muted underline underline-offset-2 hover:text-fg"
                          onClick={() => onJump(f.block_id!)}
                        >
                          문단 {no + 1}
                        </button>
                      ) : null}
                    </div>
                    <p className="mt-0.5 leading-5">{f.message}</p>
                    {f.sentence ? <p className="mt-1 text-xs text-muted">“{f.sentence}”</p> : null}
                    {extra ? <p className="mt-1 text-xs text-muted">{extra}</p> : null}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      <section>
        <h3 className="mb-2 text-[13px] font-semibold">요건 가시성 (이 문서)</h3>
        <VisibilityTable rows={result.requirements} />
      </section>
    </div>
  );
}
