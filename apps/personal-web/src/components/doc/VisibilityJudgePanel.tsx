// 요건 가시성 판정 모델 (참고 의견, POST /core/applications/{id}/visibility-judge).
// 단어 일치 기준선 옆에 "이 문서만 읽었을 때 요건 근거가 보이는가"와 문서 속 인용을 보인다.
// 판정은 저장·검사 결과를 바꾸지 않는다. 같은 요건·같은 글이면 서버 캐시(c0022)에서 꺼내 다시 돈을 쓰지 않는다.
import type { JudgeVerdict, VisibilityJudge } from "@career-brain/client";
import { Badge, Button, type BadgeTone } from "@career-brain/ui";
import { useMutation } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { ErrorText } from "../common";
import { api } from "../../lib/api";
import { usd } from "../../lib/format";
import { VISIBILITY } from "../../lib/labels";

const VERDICT: Record<JudgeVerdict["verdict"], { label: string; tone: BadgeTone }> = {
  yes: { label: "근거 보임", tone: "ok" },
  no: { label: "안 보임", tone: "none" },
  unclear: { label: "판단 어려움", tone: "warn" },
};

const STATUS: Record<string, string> = {
  ok: "확인함",
  partial: "일부만 확인함",
  unavailable: "쓸 수 없음",
  denied: "거부됨",
  error: "오류",
};

const NOTE: Record<string, string> = {
  no_route: "models.toml에 judge_visibility 라우트가 없습니다",
  egress_scope_required: "외부 모델로 보낼 권한(egress)이 없습니다",
  quote_not_found: "모델이 준 인용을 문서에서 찾지 못해 '판단 어려움'으로 내렸습니다",
};

interface SavedBlock {
  id: string | null;
  text: string;
}

/** 인용이 들어 있는 문단과 그 안의 위치. 서버가 문서에서 찾은 인용이라 대개 그대로 있다. */
function locate(blocks: SavedBlock[], quote: string) {
  for (const b of blocks) {
    const i = b.text.indexOf(quote);
    if (b.id && i >= 0) return { id: b.id, text: b.text, start: i, end: i + quote.length };
  }
  return null;
}

function QuoteInContext({ text, start, end }: { text: string; start: number; end: number }) {
  const from = Math.max(0, start - 40);
  const to = Math.min(text.length, end + 40);
  return (
    <span>
      {from > 0 ? "…" : ""}
      {text.slice(from, start)}
      <mark className="rounded bg-warn-soft px-0.5 text-fg">{text.slice(start, end)}</mark>
      {text.slice(end, to)}
      {to < text.length ? "…" : ""}
    </span>
  );
}

export function VisibilityJudgePanel({
  appId,
  docId,
  dirty,
  blocks,
  onJump,
  blockNo,
}: {
  appId: string;
  docId: string;
  dirty: boolean;
  blocks: SavedBlock[];
  onJump: (blockId: string) => void;
  blockNo: (blockId: string) => number | null;
}) {
  const run = useMutation({ mutationFn: () => api.visibilityJudge(appId, docId) });
  const res: VisibilityJudge | undefined = run.data;
  return (
    <section className="mt-4 space-y-2 rounded-lg border border-dashed border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[13px] font-semibold">판정 모델 의견 (참고)</h3>
        <Button size="sm" variant="ghost" disabled={run.isPending} onClick={() => run.mutate()}>
          <Sparkles size={13} /> {res ? "다시 확인" : "판정 모델로 확인"}
        </Button>
      </div>
      <p className="text-xs text-muted">
        저장한 글을 외부 모델에 보내 “이 문서만 읽고 요건 근거가 보이는가”를 묻습니다. 참고 의견일 뿐 검사 결과·충족을
        바꾸지 않습니다. 제한 등급 근거가 연결된 문단은 보내지 않습니다.
        {dirty ? " 저장하지 않은 편집은 보내지 않습니다." : ""}
      </p>
      <ErrorText error={run.error} />
      {res ? (
        <>
          <p className="text-xs text-muted">
            {STATUS[res.judge.status] ?? res.judge.status}
            {res.judge.note ? ` — ${NOTE[res.judge.note] ?? res.judge.note}` : ""}
            {res.judge.model ? ` · ${res.judge.model}` : ""}
            {res.judge.calls ? ` · 호출 ${res.judge.calls}회` : ""}
            {res.judge.cached ? ` · 캐시 ${res.judge.cached}회(비용 없음)` : ""}
            {res.judge.cost_usd && res.judge.calls ? ` · ${usd(res.judge.cost_usd)}` : ""}
            {res.judge.cost_basis === "list_price_equivalent" ? " (구독 환산)" : ""}
            {res.blocks_withheld ? ` · 보내지 않은 문단 ${res.blocks_withheld}개` : ""}
          </p>
          <ul className="space-y-1.5">
            {res.rows.map((r, i) => {
              const v = r.judge;
              const at = v?.quote ? locate(blocks, v.quote) : null;
              const no = at ? blockNo(at.id) : null;
              const base = VISIBILITY[r.baseline_verdict as keyof typeof VISIBILITY];
              return (
                <li key={`${r.requirement}-${i}`} className="rounded-md border border-line bg-surface px-2.5 py-2 text-[13px]">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="min-w-0 flex-1 font-medium">{r.requirement}</span>
                    {v ? <Badge tone={VERDICT[v.verdict].tone}>판정 모델: {VERDICT[v.verdict].label}</Badge> : null}
                    <Badge tone="neutral">기준선: {base?.label ?? r.baseline_verdict}</Badge>
                    {r.agrees === false ? <Badge tone="warn">기준선과 다름</Badge> : null}
                  </div>
                  {v?.quote ? (
                    <p className="mt-1 border-l-2 border-warn pl-2 text-xs leading-5">
                      {at ? <QuoteInContext text={at.text} start={at.start} end={at.end} /> : <mark className="rounded bg-warn-soft px-0.5">{v.quote}</mark>}
                      {at && no !== null ? (
                        <button className="ml-1.5 text-accent hover:underline" onClick={() => onJump(at.id)}>
                          문단 {no + 1}
                        </button>
                      ) : null}
                    </p>
                  ) : null}
                  {v?.reason ? <p className="mt-1 text-xs text-muted">{v.reason}</p> : null}
                  {v?.note ? <p className="mt-1 text-xs text-muted">{NOTE[v.note] ?? v.note}</p> : null}
                  {!v && res.judge.status !== "unavailable" ? <p className="mt-1 text-xs text-muted">이 요건에는 의견이 없습니다.</p> : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
    </section>
  );
}
