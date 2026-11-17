import type { CompanySummary } from "@career-brain/client";
import { Button, cn, Input, Sheet } from "@career-brain/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { CompanyForm, EMPTY_COMPANY, toCompanyBody } from "../components/companies/CompanyForm";
import { Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { hostOf } from "../lib/format";

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";

/** 참고 › 회사: 지원할 회사마다 모은 공개 정보. 내 경험과 따로 둔다. */
export function CompaniesPage() {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(draft.trim()), 250);
    return () => clearTimeout(t);
  }, [draft]);
  const list = useQuery({
    queryKey: ["companies", q],
    queryFn: () => api.companies(q),
    placeholderData: keepPreviousData,
  });
  const empty = !q && list.data?.length === 0;

  return (
    <div>
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="회사"
        subtitle="지원할 회사의 공개 정보를 원문 인용과 함께 모읍니다. 참고 자료라 문서 근거로 쓰지 않습니다."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus size={15} /> 회사 추가
          </Button>
        }
      />
      {!empty ? (
        <div className="mb-6">
          <Input
            type="search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="회사 이름으로 찾기"
            aria-label="회사 찾기"
            maxLength={200}
          />
        </div>
      ) : null}
      <Loaded query={list}>
        {(rows) =>
          empty ? (
            <p className="border-t border-line pt-3 text-sm text-muted">
              아직 회사가 없습니다. 회사를 추가하고 회사 소개·채용 페이지 주소나 글을 넣으면 인재상·주요 사업 같은 항목을
              정리합니다.{" "}
              <button type="button" onClick={() => setAdding(true)} className="text-fg underline underline-offset-2">
                회사 추가
              </button>
            </p>
          ) : rows.length === 0 ? (
            <p className="border-t border-line pt-3 text-sm text-muted">‘{q}’와 맞는 회사가 없습니다.</p>
          ) : (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((c) => (
                <CompanyRow key={c.id} c={c} />
              ))}
            </ul>
          )
        }
      </Loaded>
      <AddCompanySheet open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

function CompanyRow({ c }: { c: CompanySummary }) {
  const facts = c.facts_confirmed + c.facts_candidate;
  const sub = [c.aliases.join(", "), c.homepage ? hostOf(c.homepage) : null].filter(Boolean).join(" · ");
  return (
    <li>
      <Link to="/brain/companies/$companyId" params={{ companyId: c.id }} className={cn(ROW, "flex items-center gap-4")}>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{c.name}</div>
          {sub ? <div className="truncate text-[13px] text-muted">{sub}</div> : null}
        </div>
        <div className="shrink-0 text-right text-[13px] leading-tight tabular-nums">
          {c.facts_candidate ? <div className="font-semibold">확인 필요 {c.facts_candidate}</div> : null}
          <div className="text-muted">
            {[facts ? `항목 ${facts}` : "항목 없음", c.postings ? `공고 ${c.postings}` : null, c.facts_stale ? `오래됨 ${c.facts_stale}` : null]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
      </Link>
    </li>
  );
}

function AddCompanySheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: api.createCompany,
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["companies"] });
      onClose();
      navigate({ to: "/brain/companies/$companyId", params: { companyId: id } });
    },
  });
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          create.reset();
          onClose();
        }
      }}
      title="회사 추가"
    >
      {open ? (
        <CompanyForm
          initial={EMPTY_COMPANY}
          submitLabel="추가"
          pending={create.isPending}
          error={create.error}
          onSubmit={(v) => create.mutate(toCompanyBody(v))}
          onCancel={onClose}
        />
      ) : null}
    </Sheet>
  );
}
