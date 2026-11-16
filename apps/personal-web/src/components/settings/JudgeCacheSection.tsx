// 판정 응답 캐시 (c0022, c0026): 크기·꺼내 쓴 횟수·아낀 비용과 비우기. API: GET/DELETE /core/judges/cache
import { Badge, Button, Card, ConfirmDialog, Section } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../lib/api";
import { int, usd } from "../../lib/format";
import { ErrorText } from "../common";

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
      <Card className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          {d ? (
            <>
              <Badge tone={d.entries ? "accent" : "neutral"}>응답 {int(d.entries)}개</Badge>
              <span className="text-muted">꺼내 쓴 횟수 {int(d.hits)}</span>
              <span className="text-muted">아낀 비용 {usd(d.saved_usd)}</span>
              <Badge tone={d.days ? "neutral" : "warn"}>{d.days ? `보관 ${d.days}일` : "캐시 안 씀"}</Badge>
            </>
          ) : q.isError ? (
            <ErrorText error={q.error} />
          ) : (
            <Badge tone="none">확인 중</Badge>
          )}
        </div>
        <p className="text-xs leading-5 text-muted">
          같은 요건·같은 글을 다시 물으면 판정 모델을 부르지 않고 이전 응답을 씁니다. 응답에는 문서 인용이 들어 있어 이 작업
          공간에만 두고, 내보내기에는 넣지 않습니다. 주장이나 지원 문서를 지우면 그 응답도 같이 지워집니다. 보관 일수는{" "}
          <code>CAREER_JUDGE_CACHE_DAYS</code>로 정합니다.
        </p>
        {cleared !== null ? <p className="text-xs text-muted">{int(cleared)}개를 지웠습니다.</p> : null}
        <ErrorText error={clear.error} />
        <Button variant="danger" size="sm" disabled={!d?.entries || clear.isPending} onClick={() => setAsking(true)}>
          캐시 비우기
        </Button>
      </Card>
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title="판정 응답 캐시를 비울까요?"
        description="다음 판정부터는 판정 모델을 다시 부릅니다 (비용이 다시 듭니다). 주장·문서·판정 기준선은 바뀌지 않습니다."
        confirmLabel="비우기"
        tone="danger"
        busy={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </Section>
  );
}
