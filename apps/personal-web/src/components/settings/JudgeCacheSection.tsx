// 판정 응답 캐시 (c0022, c0026): 크기·꺼내 쓴 횟수·아낀 비용과 비우기. API: GET/DELETE /core/judges/cache
import { Button, ConfirmDialog, Section, toast } from "@career-brain/ui";
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
  const clear = useMutation({
    mutationFn: api.clearJudgeCache,
    onSuccess: (r) => {
      setAsking(false);
      toast(`저장해 둔 의견 ${int(r.deleted)}개를 비웠습니다.`);
      qc.invalidateQueries({ queryKey: ["judge-cache"] });
    },
  });
  const d = q.data;
  return (
    <Section title="저장해 둔 AI 의견">
      {d ? (
        <dl className="divide-y divide-line border-y border-line">
          <Row label="저장된 응답">{int(d.entries)}개</Row>
          <Row label="꺼내 쓴 횟수">{int(d.hits)}회</Row>
          <Row label="아낀 비용">{usd(d.saved_usd)}</Row>
          <Row label="보관">{d.days ? `${d.days}일` : <span className="font-medium">저장 안 함</span>}</Row>
        </dl>
      ) : q.isError ? (
        <ErrorText error={q.error} />
      ) : (
        <p className="border-t border-line pt-3 text-sm text-muted">확인 중</p>
      )}
      <p className="text-[13px] leading-5 text-muted">
        같은 요건과 같은 글은 AI를 다시 부르지 않고 지난 의견을 씁니다. 이 작업 공간 밖으로 내보내지
        않습니다.
      </p>
      <ErrorText error={clear.error} />
      <Button variant="danger" size="sm" disabled={!d?.entries || clear.isPending} onClick={() => setAsking(true)}>
        비우기
      </Button>
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title="저장해 둔 AI 의견을 비울까요?"
        description="다음 확인부터 AI를 다시 부르며 비용이 듭니다. 확인한 사실과 문서는 바뀌지 않습니다."
        confirmLabel="비우기"
        tone="danger"
        busy={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </Section>
  );
}
