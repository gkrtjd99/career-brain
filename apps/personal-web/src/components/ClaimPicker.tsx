import type { ClaimStatus, SearchHit } from "@career-brain/client";
import { Button, EmptyState, Input, Sheet, StatusBadge } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Check, Search as SearchIcon } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { Loaded } from "./common";

const CONFIRMED: ClaimStatus[] = ["self_confirmed", "evidence_supported"];

export interface PickedClaim {
  id: string;
  statement: string;
  status: ClaimStatus | string;
  experience_title: string | null;
}

/** 브레인 주장 고르기 (검색). 확인된 주장만 근거로 세고, 확인 전 주장은 경고와 함께 고를 수 있다. */
export function ClaimPicker({
  open,
  onClose,
  onPick,
  selected = [],
  title = "근거 주장 연결",
  initialQuery = "",
}: {
  open: boolean;
  onClose: () => void;
  onPick: (claim: PickedClaim) => void;
  selected?: string[];
  title?: string;
  initialQuery?: string;
}) {
  const [draft, setDraft] = useState(initialQuery);
  const [q, setQ] = useState(initialQuery);
  const result = useQuery({
    queryKey: ["search", "claim-picker", q],
    queryFn: ({ signal }) => api.search(q, { kinds: ["claim"], limit: 30 }, signal),
    enabled: open && q.trim().length > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description="확인한 주장만 근거로 셉니다. 확인 전 주장은 브레인에서 먼저 확인하세요."
    >
      <form
        role="search"
        className="mb-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(draft.trim());
        }}
      >
        <Input
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="예: 응답 시간, Kafka, 결제"
          aria-label="주장 검색어"
          maxLength={500}
          autoFocus
        />
        <Button type="submit" variant="primary" aria-label="검색">
          <SearchIcon size={16} />
        </Button>
      </form>
      {!q ? (
        <EmptyState>문단 내용과 관련된 낱말로 주장을 찾습니다.</EmptyState>
      ) : (
        <Loaded query={result}>
          {(r) =>
            r.hits.length ? (
              <ul className="space-y-2">
                {r.hits.map((h: SearchHit) => {
                  const on = selected.includes(h.id);
                  const confirmed = CONFIRMED.includes(h.claim_status as ClaimStatus);
                  return (
                    <li key={h.id} className="rounded-lg border border-line bg-surface p-2.5">
                      <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                        {h.claim_status ? <StatusBadge status={h.claim_status} /> : null}
                        <span className="truncate">{h.experience_title}</span>
                      </div>
                      <p className="text-sm leading-6">{h.snippet}</p>
                      {!confirmed ? (
                        <p className="mt-1 text-xs text-warn">확인 전 주장은 근거로 세지 않습니다.</p>
                      ) : null}
                      <div className="mt-1.5">
                        <Button
                          size="sm"
                          variant={on ? "ghost" : "secondary"}
                          disabled={on}
                          onClick={() =>
                            onPick({
                              id: h.id,
                              statement: h.snippet,
                              status: h.claim_status ?? "extracted",
                              experience_title: h.experience_title,
                            })
                          }
                        >
                          {on ? (
                            <>
                              <Check size={14} /> 연결됨
                            </>
                          ) : (
                            "연결"
                          )}
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyState>찾은 주장이 없습니다.</EmptyState>
            )
          }
        </Loaded>
      )}
    </Sheet>
  );
}
