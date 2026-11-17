import type { Credential, CredentialKind } from "@career-brain/client";
import { Button, cn, FilterChips } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { CredentialSheet } from "../components/credentials/CredentialSheet";
import { api } from "../lib/api";
import {
  CREDENTIAL_KIND_LABEL,
  CREDENTIAL_KIND_OPTIONS,
  CREDENTIAL_STATUS_LABEL,
  expiryMark,
  scoreText,
  whenText,
} from "../lib/credentials";
import { formatDate } from "../lib/dates";

const route = getRouteApi("/brain/credentials");

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const COLS = "sm:grid-cols-[4.5rem_minmax(0,1.6fr)_6rem_6.5rem_7.5rem_5rem]";

type SheetState = { credential: Credential | null } | null;

/** 브레인 › 나 › 자격·이수: 표 하나. 증빙에서 뽑은 후보는 맨 위에서 확인한다. */
export function CredentialsPage() {
  const { type: kind } = route.useSearch();
  const navigate = useNavigate();
  const [sheet, setSheet] = useState<SheetState>(null);
  const q = useQuery({ queryKey: ["credentials", "all"], queryFn: () => api.credentials() });
  const setKind = (k: CredentialKind | undefined) =>
    navigate({ to: "/brain/credentials", search: k ? { type: k } : {}, replace: true });

  return (
    <div>
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="자격·이수"
        subtitle="만료일을 적으면 오늘 일정에 나옵니다."
        actions={
          <Button variant="primary" onClick={() => setSheet({ credential: null })}>
            <Plus size={15} /> 기록 추가
          </Button>
        }
      />
      <Loaded query={q}>
        {(rows) => {
          const candidates = rows.filter((c) => c.status === "extracted");
          const records = rows.filter((c) => c.status !== "extracted");
          const kinds = CREDENTIAL_KIND_OPTIONS.filter((o) => records.some((c) => c.kind === o.value));
          const shown = kind ? records.filter((c) => c.kind === kind) : records;
          return (
            <div className="space-y-10">
              {candidates.length ? (
                <Candidates rows={candidates} onOpen={(c) => setSheet({ credential: c })} />
              ) : null}

              {records.length ? (
                <section>
                  {kinds.length > 1 ? (
                    <FilterChips<CredentialKind | undefined>
                      label="종류 필터"
                      value={kind}
                      onChange={setKind}
                      items={[
                        { value: undefined, label: "전체", count: records.length },
                        ...kinds.map((o) => ({
                          value: o.value,
                          label: o.label,
                          count: records.filter((c) => c.kind === o.value).length,
                        })),
                      ]}
                      className="mb-3"
                    />
                  ) : null}
                  {shown.length ? (
                    <CredentialTable rows={shown} onOpen={(c) => setSheet({ credential: c })} />
                  ) : (
                    <p className="border-t border-line pt-3 text-sm text-muted">
                      {CREDENTIAL_KIND_LABEL[kind!]} 기록이 없습니다.{" "}
                      <button
                        type="button"
                        onClick={() => setKind(undefined)}
                        className="text-fg underline underline-offset-2"
                      >
                        필터 초기화
                      </button>
                    </p>
                  )}
                </section>
              ) : !candidates.length ? (
                <p className="border-t border-line pt-3 text-sm text-muted">
                  아직 기록이 없습니다. 자격증·성적표 사본을{" "}
                  <Link to="/brain/proofs" className="text-fg underline underline-offset-2">
                    증빙 서류
                  </Link>
                  로 올리면 항목을 뽑아 여기에 올립니다. 직접 추가할 수도 있습니다.
                </p>
              ) : null}
            </div>
          );
        }}
      </Loaded>
      <CredentialSheet open={sheet !== null} credential={sheet?.credential ?? null} onClose={() => setSheet(null)} />
    </div>
  );
}

/** 맨 위 "확인할 것": 증빙에서 뽑았지만 아직 사람이 보지 않은 항목 */
function Candidates({ rows, onOpen }: { rows: Credential[]; onOpen: (c: Credential) => void }) {
  const qc = useQueryClient();
  const confirm = useMutation({
    mutationFn: (id: string) => api.confirmCredential(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["credentials"] });
      void qc.invalidateQueries({ queryKey: ["sources", "proofs"] });
    },
  });
  return (
    <section>
      <h2 className="text-[15px] font-semibold">
        확인할 것 <span className="font-normal text-muted tabular-nums">{rows.length}</span>
      </h2>
      <p className="mt-0.5 text-[13px] text-muted">
        증빙 서류에서 뽑았습니다. 맞으면 확인합니다.
      </p>
      <ErrorText error={confirm.error} className="mt-2" />
      <ul className="mt-3 divide-y divide-line border-y border-line">
        {rows.map((c) => (
          <li key={c.id} className="flex items-center gap-2">
            <button type="button" onClick={() => onOpen(c)} className={cn(ROW, "min-w-0 flex-1 text-left")}>
              <div className="truncate text-sm font-medium">{c.name}</div>
              <div className="line-clamp-2 text-[13px] text-muted tabular-nums">
                {[
                  CREDENTIAL_KIND_LABEL[c.kind],
                  c.issuer,
                  scoreText(c),
                  whenText(c),
                  c.expires_on ? `만료 ${formatDate(c.expires_on)}` : null,
                  c.source_title ? `원본 ${c.source_title}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </button>
            <Button
              size="sm"
              className="shrink-0 max-sm:h-9"
              onClick={() => confirm.mutate(c.id)}
              disabled={confirm.isPending}
            >
              확인
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CredentialTable({ rows, onOpen }: { rows: Credential[]; onOpen: (c: Credential) => void }) {
  return (
    <div>
      <div className={cn("hidden gap-4 border-b border-line pb-2 text-xs text-muted sm:grid", COLS)}>
        <span>종류</span>
        <span>이름</span>
        <span>점수</span>
        <span>취득</span>
        <span>만료</span>
        <span>상태</span>
      </div>
      <ul className="divide-y divide-line border-b border-line max-sm:border-t">
        {rows.map((c) => (
          <CredentialRow key={c.id} c={c} onOpen={() => onOpen(c)} />
        ))}
      </ul>
    </div>
  );
}

function CredentialRow({ c, onOpen }: { c: Credential; onOpen: () => void }) {
  const mark = expiryMark(c);
  const when = whenText(c);
  const score = scoreText(c);
  return (
    <li>
      <button type="button" onClick={onOpen} className={cn(ROW, "grid w-full gap-0.5 text-left text-[13px] sm:items-center sm:gap-4", COLS)}>
        <span className="hidden text-muted sm:block">{CREDENTIAL_KIND_LABEL[c.kind]}</span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{c.name}</span>
          {c.issuer ? <span className="block truncate text-muted">{c.issuer}</span> : null}
        </span>
        {/* 좁은 화면: 종류·점수·날짜·만료·상태를 한 줄로 */}
        <span className="line-clamp-2 text-muted tabular-nums sm:hidden">
          {[
            CREDENTIAL_KIND_LABEL[c.kind],
            score,
            when,
            c.expires_on ? `만료 ${formatDate(c.expires_on)}` : null,
            CREDENTIAL_STATUS_LABEL[c.status],
          ]
            .filter(Boolean)
            .join(" · ")}
          {mark ? <span className="font-semibold text-fg"> · {mark}</span> : null}
        </span>
        <span className="hidden truncate tabular-nums sm:block">{score ?? <Dash />}</span>
        <span className="hidden truncate text-muted tabular-nums sm:block">{when ?? <Dash />}</span>
        <span className="hidden leading-tight tabular-nums sm:block">
          {c.expires_on ? (
            <>
              <span className={mark ? undefined : "text-muted"}>{formatDate(c.expires_on)}</span>
              {mark ? <span className="block font-semibold">{mark}</span> : null}
            </>
          ) : (
            <Dash />
          )}
        </span>
        <span className="hidden text-muted sm:block">{CREDENTIAL_STATUS_LABEL[c.status]}</span>
      </button>
    </li>
  );
}

function Dash() {
  return (
    <span className="text-muted" aria-label="없음">
      –
    </span>
  );
}
