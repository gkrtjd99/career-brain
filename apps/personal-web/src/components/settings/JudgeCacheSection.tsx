// 판정 응답 캐시 (c0022, c0026): 크기·꺼내 쓴 횟수·아낀 비용과 비우기. API: GET/DELETE /core/judges/cache
import { Button, ConfirmDialog, Section } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { int, usd } from "../../lib/format";
import { ErrorText } from "../common";

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 text-sm">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 text-right tabular-nums">{children}</dd>
    </div>
  );
}

/** 판정 모델(참고 의견) 응답 캐시. 문서 글을 밖으로 보내는 권한(egress)이 있는 사람에게만 보인다. */
export function JudgeCacheSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["judge-cache"], queryFn: api.judgeCache });
  const [asking, setAsking] = useState(false);
  const [cleared, setCleared] = useState<number | null>(null);
  const clear = useMutation({
    mutationFn: api.clearJudgeCache,
    onSuccess: (r) => {
      setAsking(false);
      setCleared(r.deleted);
      qc.invalidateQueries({ queryKey: ["judge-cache"] });
    },
  });
  const d = q.data;
  return (
    <Section title="판정 응답 캐시">
      {d ? (
        <dl className="divide-y divide-line border-y border-line">
          <Row label="저장된 응답">{int(d.entries)}개</Row>
          <Row label="꺼내 쓴 횟수">{int(d.hits)}회</Row>
          <Row label="아낀 비용">{usd(d.saved_usd)}</Row>
          <Row label="보관">{d.days ? `${d.days}일` : <span className="font-medium">캐시 안 씀</span>}</Row>
        </dl>
      ) : q.isError ? (
        <ErrorText error={q.error} />
      ) : (
        <p className="border-t border-line pt-3 text-sm text-muted">확인 중</p>
      )}
      <p className="text-[13px] leading-5 text-muted">
        같은 요건·같은 글을 다시 물으면 판정 모델을 부르지 않고 이전 응답을 씁니다. 응답에는 문서 인용이 들어 있어 이
        작업 공간에만 두고, 내보내기에는 넣지 않습니다. 사실이나 지원 문서를 삭제하면 그 응답도 같이 삭제됩니다. 보관
        일수는 <code>CAREER_JUDGE_CACHE_DAYS</code>로 정합니다.
      </p>
      {cleared !== null ? <p className="text-[13px] text-muted">{int(cleared)}개를 지웠습니다.</p> : null}
      <ErrorText error={clear.error} />
      <Button variant="danger" size="sm" disabled={!d?.entries || clear.isPending} onClick={() => setAsking(true)}>
        캐시 비우기
      </Button>
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title="판정 응답 캐시를 비울까요?"
        description="다음 판정부터는 판정 모델을 다시 부릅니다 (비용이 다시 듭니다). 확인한 사실·문서·판정 기준선은 바뀌지 않습니다."
        confirmLabel="비우기"
        tone="danger"
        busy={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </Section>
  );
}
