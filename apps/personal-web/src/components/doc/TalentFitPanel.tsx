// 인재상 맞춤 확인 (참고 의견, POST /core/applications/{id}/talent-fit).
// 공고·회사 인재상 줄마다 "이 글의 어느 문단이 글쓴이가 한 일로 그 모습을 보여 주는가"를 판정 모델(TypeSafe Jev)에
// 묻는다. 말로 내세우는 것("저는 도전적인 사람입니다")은 드러난 것으로 치지 않는다. 근거 문단으로 이동할 수 있다.
// 결과는 저장하지 않는다.
import type { TalentFit, TalentLevel } from "@career-brain/client";
import { Button, cn, Disclosure, type BadgeTone } from "@career-brain/ui";
import { useMutation } from "@tanstack/react-query";
import { ErrorText } from "../common";
import { api } from "../../lib/api";
import { usd } from "../../lib/format";
import { toneText } from "../../pages/Application";

const LEVEL: Record<TalentLevel, { label: string; tone: BadgeTone }> = {
  shown: { label: "한 일로 드러남", tone: "ok" },
  claimed: { label: "말로만 보임", tone: "warn" },
  absent: { label: "안 보임", tone: "none" },
  unclear: { label: "판단 갈림", tone: "none" },
};

const STATUS: Record<string, string> = {
  ok: "확인함",
  partial: "일부만 확인함",
  unavailable: "쓸 수 없음",
  denied: "거부됨",
  error: "오류",
};

const NOTE: Record<string, string> = {
  no_route: "Jev를 연결하지 않았습니다. 연결·관리에서 TypeSafe 연결을 더하고, 용도별 모델의 인재상 맞춤 판정에 고릅니다.",
  api_key_missing: "Jev 연결에 키가 없습니다. 연결·관리에서 키를 넣습니다.",
  egress_scope_required: "이 연결은 문서를 밖으로 보낼 수 없습니다.",
  no_traits: "공고와 회사 정보에서 인재상을 찾지 못했습니다. 공고를 다시 가져오거나 회사 정보를 찾아 봅니다.",
  empty_document: "저장한 글이 없습니다.",
  "denied:egress_denied": "Jev 연결이 개인 자료를 받지 않게 되어 있습니다. 연결의 보낼 자료에 개인을 켭니다.",
  "denied:budget_exceeded": "이번 달 예산을 다 썼습니다.",
  "denied:price_unknown": "이 모델의 단가를 몰라 부르지 않았습니다.",
  unauthorized: "Jev 키가 맞지 않습니다.",
  rate_limited: "요청이 잦아 잠시 막혔습니다. 조금 뒤 다시 확인합니다.",
};

interface SavedBlock {
  id: string | null;
  text: string;
}

function excerpt(text: string, max = 90) {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

export function TalentFitPanel({
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
  const textOf = (id: string) => blocks.find((b) => b.id === id)?.text ?? null;
  const run = useMutation({ mutationFn: () => api.talentFit(appId, docId) });
  const res: TalentFit | undefined = run.data;
  const rows = (res?.traits ?? []).map((t) => ({ trait: t, fit: t.fits.find((f) => f.document_id === docId) ?? null }));
  const counts = { shown: 0, claimed: 0, absent: 0, unclear: 0 };
  for (const r of rows) if (r.fit) counts[r.fit.level] += 1;
  const judged = rows.some((r) => r.fit);
  return (
    <section className="mt-8 space-y-2">
      <h3 className="text-sm font-medium">인재상 맞춤 (참고)</h3>
      <Button size="sm" disabled={run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? "확인하는 중" : res ? "인재상 다시 확인" : "인재상에 맞는지 확인"}
      </Button>
      <Disclosure title="무엇을 보내나요" className="text-xs text-muted">
        <p className="mt-1 leading-5">
          저장한 글과 공고·회사 정보의 인재상 줄을 판정 모델(TypeSafe Jev)에 보내, 줄마다 “어느 문단이 글쓴이가 한
          일로 이 모습을 보여 주는가”를 묻습니다. 남이 한 일, 앞으로의 계획, 이력·직함만 적은 문단은 근거로 치지 않습니다. “저는 도전적인 사람입니다”처럼 말로만 내세운 것은 드러난 것으로 치지 않습니다. 참고
          의견일 뿐 저장하지 않습니다. 이 기기 밖으로 보내지 않는 근거가 연결된 문단은 보내지 않습니다.
          {dirty ? " 저장하지 않은 편집은 보내지 않습니다." : ""}
        </p>
      </Disclosure>
      <ErrorText error={run.error} />
      {res ? (
        <>
          <p className="text-xs text-muted tabular-nums">
            {STATUS[res.judge.status] ?? res.judge.status}
            {res.judge.note ? ` — ${NOTE[res.judge.note] ?? res.judge.note}` : ""}
            {judged
              ? ` · 드러남 ${counts.shown} · 말로만 ${counts.claimed} · 안 보임 ${counts.absent}` +
                (counts.unclear ? ` · 갈림 ${counts.unclear}` : "")
              : ""}
            {res.judge.cost_usd && res.judge.calls ? ` · ${usd(res.judge.cost_usd)}` : ""}
            {res.blocks_withheld ? ` · 보내지 않은 문단 ${res.blocks_withheld}개` : ""}
          </p>
          {rows.length ? (
            <ul className="divide-y divide-line border-t border-line">
              {rows.map(({ trait, fit }, i) => (
                <li key={`${trait.text}-${i}`} className="py-2.5 text-[13px]">
                  <p className="font-medium">{trait.text}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
                    {fit ? (
                      <span className={cn(toneText(LEVEL[fit.level].tone))}>{LEVEL[fit.level].label}</span>
                    ) : null}
                    <span>
                      {fit ? "· " : ""}
                      {trait.source === "posting" ? "공고" : trait.confirmed ? "회사 정보" : "회사 정보 (확인 전)"}
                    </span>
                    {(fit?.evidence ?? []).map((id) => {
                      const no = blockNo(id);
                      return no === null ? null : (
                        <button
                          key={id}
                          type="button"
                          className="min-h-9 px-1 text-muted underline underline-offset-2 hover:text-fg"
                          onClick={() => onJump(id)}
                        >
                          문단 {no + 1}
                        </button>
                      );
                    })}
                  </p>
                  {fit?.evidence[0] && textOf(fit.evidence[0]) ? (
                    <p className="mt-1 text-xs leading-5 text-muted">“{excerpt(textOf(fit.evidence[0]) ?? "")}”</p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
