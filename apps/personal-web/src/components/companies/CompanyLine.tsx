import type { Posting } from "@career-brain/client";
import { Button, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { ErrorText } from "../common";

/** 공고 화면의 회사 한 줄. 연결돼 있으면 요약, 아니면 연결 입구. */
export function CompanyLine({ p }: { p: Posting }) {
  const [linking, setLinking] = useState(false);
  const name =
    (p.fields.company as string | undefined) ?? (p.latest?.extracted.company?.value as string | undefined) ?? "";
  const linked = useQuery({
    queryKey: ["company", p.company_id],
    queryFn: () => api.company(p.company_id!),
    enabled: !!p.company_id,
  });

  if (p.company_id) {
    const c = linked.data;
    const confirmed = c?.facts.filter((f) => f.status === "confirmed").length ?? 0;
    const stale = c?.facts.filter((f) => f.status === "confirmed" && f.stale).length ?? 0;
    return (
      <div className="mb-6 border-y border-line">
        <Link
          to="/brain/companies/$companyId"
          params={{ companyId: p.company_id }}
          className="-mx-2 flex min-h-9 items-center gap-2 rounded-md px-2 py-2 text-[13px] transition-colors duration-150 hover:bg-subtle/60"
        >
          <span className="text-muted">회사</span>
          <span className="min-w-0 truncate font-medium">{c?.name ?? name}</span>
          {c ? (
            <span className="text-muted tabular-nums">
              {[confirmed ? `확인한 항목 ${confirmed}` : "확인한 항목 없음", stale ? `오래됨 ${stale}` : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
          ) : null}
          <ChevronRight size={14} className="ml-auto shrink-0 text-muted" />
        </Link>
      </div>
    );
  }

  return (
    <div className="mb-6 flex min-h-9 items-center gap-2 border-y border-line py-1 text-[13px]">
      <span className="text-muted">회사</span>
      <span className="min-w-0 truncate">{name || "모름"}</span>
      <button
        type="button"
        onClick={() => setLinking(true)}
        className="ml-auto inline-flex min-h-9 items-center text-fg underline underline-offset-2"
      >
        회사 연결
      </button>
      <LinkSheet postingId={p.id} name={name} open={linking} onClose={() => setLinking(false)} />
    </div>
  );
}

function LinkSheet({
  postingId,
  name,
  open,
  onClose,
}: {
  postingId: string;
  name: string;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const matches = useQuery({
    queryKey: ["companies", name],
    queryFn: () => api.companies(name),
    enabled: open,
  });
  const done = (companyId: string) => {
    qc.invalidateQueries({ queryKey: ["posting", postingId] });
    qc.invalidateQueries({ queryKey: ["postings"] });
    qc.invalidateQueries({ queryKey: ["companies"] });
    qc.invalidateQueries({ queryKey: ["company", companyId] });
    onClose();
  };
  const link = useMutation({
    mutationFn: (companyId: string) => api.linkCompanyPostings(companyId, [postingId]).then(() => companyId),
    onSuccess: done,
  });
  const create = useMutation({
    mutationFn: async () => {
      const { id } = await api.createCompany({ name: name.trim(), aliases: [] });
      await api.linkCompanyPostings(id, [postingId]);
      return id;
    },
    onSuccess: done,
  });
  const rows = matches.data ?? [];
  const busy = link.isPending || create.isPending;
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          link.reset();
          create.reset();
          onClose();
        }
      }}
      title="회사 연결"
      description="같은 회사의 공고를 모으고, 회사에서 모은 정보를 이 공고 옆에 보여 줍니다."
    >
      <div className="space-y-6">
        {rows.length ? (
          <ul className="divide-y divide-line border-y border-line">
            {rows.map((c) => (
              <li key={c.id} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{c.name}</div>
                  {c.aliases.length ? (
                    <div className="truncate text-[13px] text-muted">{c.aliases.join(", ")}</div>
                  ) : null}
                </div>
                <Button size="sm" className="max-sm:h-9" onClick={() => link.mutate(c.id)} disabled={busy}>
                  연결
                </Button>
              </li>
            ))}
          </ul>
        ) : matches.isSuccess ? (
          <p className="border-t border-line pt-3 text-sm text-muted">
            {name ? `‘${name}’와 맞는 회사가 없습니다.` : "공고에 회사 이름이 없습니다."}
          </p>
        ) : null}
        {name ? (
          <div className="border-t border-line pt-4">
            <Button variant={rows.length ? "secondary" : "primary"} onClick={() => create.mutate()} disabled={busy}>
              ‘{name}’ 회사 추가하고 연결
            </Button>
          </div>
        ) : (
          <p className="text-[13px] text-muted">
            <Link to="/brain/companies" className="text-fg underline underline-offset-2">
              회사
            </Link>{" "}
            화면에서 회사를 추가한 뒤 연결합니다.
          </p>
        )}
        <ErrorText error={matches.error ?? link.error ?? create.error} />
      </div>
    </Sheet>
  );
}
