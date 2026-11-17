// 지원 작업대의 "회사" 칸. 공고에 연결한 회사의 확인한 항목을 기준일·출처와 함께 보인다.
// 회사 항목은 참고 지식이다: 내 경험의 근거가 아니다. 연결이 없으면 공고 화면과 같은 "회사 연결".
import { EmptyState } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Loaded } from "../common";
import { CompanyLine } from "../companies/CompanyLine";
import { api } from "../../lib/api";
import { asOfLabel, FACT_KIND_LABEL, FACT_KIND_ORDER } from "../../lib/companies";
import { formatDate } from "../../lib/dates";
import { hostOf } from "../../lib/format";

export function CompanyPanel({ postingId }: { postingId: string }) {
  const posting = useQuery({ queryKey: ["posting", postingId], queryFn: () => api.posting(postingId) });
  return (
    <Loaded query={posting}>
      {(p) => (
        <div className="space-y-4">
          <CompanyLine p={p} />
          {p.company_id ? <Facts companyId={p.company_id} /> : null}
        </div>
      )}
    </Loaded>
  );
}

function Facts({ companyId }: { companyId: string }) {
  const q = useQuery({ queryKey: ["company", companyId], queryFn: () => api.company(companyId) });
  return (
    <Loaded query={q}>
      {(c) => {
        const facts = c.facts
          .filter((f) => f.status === "confirmed")
          .sort((a, b) => FACT_KIND_ORDER.indexOf(a.kind) - FACT_KIND_ORDER.indexOf(b.kind));
        if (!facts.length)
          return (
            <EmptyState>
              확인한 회사 항목이 없습니다.{" "}
              <Link
                to="/brain/companies/$companyId"
                params={{ companyId }}
                className="text-fg underline underline-offset-2"
              >
                회사 화면에서 확인
              </Link>
            </EmptyState>
          );
        return (
          <>
            <ul className="divide-y divide-line border-y border-line">
              {facts.map((f) => {
                const asOf = asOfLabel(f.as_of);
                const source = f.citation.source_url ? hostOf(f.citation.source_url) : "붙여넣은 글";
                return (
                  <li key={f.id} className="py-2.5 text-[13px]">
                    <p className="text-xs text-muted">{FACT_KIND_LABEL[f.kind] ?? f.kind}</p>
                    <p className="mt-0.5 leading-5">{f.body}</p>
                    <p className="mt-0.5 text-xs text-muted tabular-nums">
                      {asOf ? `기준일 ${asOf}` : "기준일 모름"} · 출처 {source} · {formatDate(f.citation.fetched_at)}{" "}
                      가져옴
                      {f.stale ? <span className="font-medium text-fg"> · 오래됨</span> : null}
                    </p>
                  </li>
                );
              })}
            </ul>
            <p className="text-xs text-muted">회사 항목은 참고입니다. 글의 근거는 내 경험의 확인한 사실만 셉니다.</p>
          </>
        );
      }}
    </Loaded>
  );
}
