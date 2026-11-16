import type { UsageGroup, UsageRow } from "@career-brain/client";
import { Badge, Card, EmptyState, Select } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { int, usd } from "../lib/format";

const GROUPS: [UsageGroup, string][] = [
  ["day", "날짜별"],
  ["model", "모델별"],
  ["purpose", "작업별"],
  ["endpoint", "엔드포인트별"],
  ["status", "결과별"],
];

const PURPOSE_LABEL: Record<string, string> = {
  brain_extract: "경험 추출",
  interview_questions: "인터뷰 질문",
  job_extract: "공고 추출",
  embedding: "검색 색인",
  search: "검색",
  ping: "연결 확인",
};

export function UsagePage() {
  const [days, setDays] = useState(30);
  const [groupBy, setGroupBy] = useState<UsageGroup>("day");
  const q = useQuery({
    queryKey: ["usage", days, groupBy],
    queryFn: () => api.usage({ days, groupBy }),
    placeholderData: keepPreviousData,
  });

  return (
    <div>
      <PageHeader title="사용량" subtitle="모델 호출 토큰과 비용. 호출마다 기록합니다." />
      <div className="mb-4 flex flex-wrap gap-2">
        <Select value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-auto" aria-label="기간">
          <option value={7}>최근 7일</option>
          <option value={30}>최근 30일</option>
          <option value={90}>최근 90일</option>
          <option value={365}>최근 1년</option>
        </Select>
        <Select
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as UsageGroup)}
          className="w-auto"
          aria-label="묶는 기준"
        >
          {GROUPS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </Select>
      </div>
      <Loaded query={q}>
        {(u) => {
          const budget = u.monthly_budget_usd ? Number(u.monthly_budget_usd) : null;
          const spent = Number(u.month_spent_usd);
          const ratio = budget ? Math.min(1, spent / budget) : 0;
          return (
            <div className={q.isFetching ? "space-y-6 opacity-70" : "space-y-6"}>
              <Card className="space-y-2 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-[13px] text-muted">이번 달 실제 청구</span>
                  <span className="text-base font-semibold tabular-nums">
                    {usd(spent, 4)}
                    {budget !== null ? <span className="text-[13px] font-normal text-muted"> / 예산 {usd(budget, 2)}</span> : null}
                  </span>
                </div>
                {budget !== null ? (
                  <div
                    className="h-1.5 overflow-hidden rounded-full bg-subtle"
                    role="meter"
                    aria-valuemin={0}
                    aria-valuemax={budget}
                    aria-valuenow={spent}
                    aria-label="이번 달 예산 사용"
                  >
                    <div
                      className={ratio >= 0.8 ? "h-full bg-warn" : "h-full bg-accent"}
                      style={{ width: `${ratio * 100}%` }}
                    />
                  </div>
                ) : (
                  <p className="text-xs text-muted">월 예산이 설정되지 않았습니다 (models.toml [budget]).</p>
                )}
                <p className="text-xs text-muted">
                  기간 합계: 호출 {int(u.total.calls)}회 · 청구 {usd(u.total.spent_usd)} · 종량제 환산{" "}
                  {usd(u.total.equivalent_usd)}
                  {u.total.unknown_cost_calls ? ` · 단가 미상 ${u.total.unknown_cost_calls}회` : ""}
                </p>
                {u.notes.map((n) => (
                  <p key={n} className="text-xs text-warn">
                    {n}
                  </p>
                ))}
              </Card>

              {u.rows.length ? (
                <>
                  {/* 넓은 화면: 표 */}
                  <div className="hidden overflow-x-auto rounded-lg border border-line bg-surface md:block">
                    <table className="w-full text-[13px] tabular-nums">
                      <thead className="border-b border-line text-left text-xs text-muted">
                        <tr>
                          <th className="px-3 py-2 font-medium">{GROUPS.find((g) => g[0] === u.group_by)?.[1].replace("별", "")}</th>
                          <th className="px-3 py-2 text-right font-medium">호출</th>
                          <th className="px-3 py-2 text-right font-medium">입력</th>
                          <th className="px-3 py-2 text-right font-medium">캐시 입력</th>
                          <th className="px-3 py-2 text-right font-medium">출력</th>
                          <th className="px-3 py-2 text-right font-medium">청구</th>
                          <th className="px-3 py-2 text-right font-medium">환산</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {[...u.rows, u.total].map((r) => (
                          <tr key={r.key} className={r === u.total ? "font-medium" : undefined}>
                            <td className="px-3 py-2">
                              <RowKey row={r} group={u.group_by} total={r === u.total} />
                            </td>
                            <td className="px-3 py-2 text-right">{int(r.calls)}</td>
                            <td className="px-3 py-2 text-right">{int(r.input_tokens)}</td>
                            <td className="px-3 py-2 text-right">{int(r.cached_input_tokens)}</td>
                            <td className="px-3 py-2 text-right">{int(r.output_tokens + r.reasoning_tokens)}</td>
                            <td className="px-3 py-2 text-right">{usd(r.spent_usd)}</td>
                            <td className="px-3 py-2 text-right text-muted">{usd(r.equivalent_usd)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {/* 좁은 화면: 목록 */}
                  <ul className="divide-y divide-line rounded-lg border border-line bg-surface md:hidden">
                    {u.rows.map((r) => (
                      <li key={r.key} className="px-3 py-2.5 text-[13px] tabular-nums">
                        <div className="flex items-center justify-between gap-2">
                          <RowKey row={r} group={u.group_by} />
                          <span className="font-medium">{usd(r.spent_usd)}</span>
                        </div>
                        <div className="text-xs text-muted">
                          {int(r.calls)}회 · 입력 {int(r.input_tokens)} · 출력 {int(r.output_tokens + r.reasoning_tokens)} · 환산{" "}
                          {usd(r.equivalent_usd)}
                        </div>
                      </li>
                    ))}
                  </ul>
                  <p className="text-xs text-muted">
                    청구는 실제로 낸 금액, 환산은 같은 토큰을 종량제 단가로 계산한 값입니다 (구독·무료 크레딧이면 청구가 0).
                  </p>
                </>
              ) : (
                <EmptyState>이 기간에 모델 호출이 없습니다.</EmptyState>
              )}
            </div>
          );
        }}
      </Loaded>
    </div>
  );
}

function RowKey({ row, group, total }: { row: UsageRow; group: UsageGroup; total?: boolean }) {
  if (total) return <>합계</>;
  const problems = row.errors + row.denied;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {group === "day" ? formatDate(row.key) : group === "purpose" ? (PURPOSE_LABEL[row.key] ?? row.key) : row.key}
      {problems ? <Badge tone="danger">오류·거부 {problems}</Badge> : null}
      {row.unknown_cost_calls ? <Badge tone="warn">단가 미상 {row.unknown_cost_calls}</Badge> : null}
    </span>
  );
}
