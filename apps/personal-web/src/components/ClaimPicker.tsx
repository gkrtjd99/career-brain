import type { ClaimStatus, SearchHit } from "@career-brain/client";
import { Button, IconButton, Input, Sheet, STATUS_LABEL, type VerificationStatus } from "@career-brain/ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Check, Search as SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
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
  title = "근거 연결",
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
  // 열 때마다 바깥에서 준 검색어(문항·문단)로 다시 시작한다
  useEffect(() => {
    if (!open) return;
    setDraft(initialQuery);
    setQ(initialQuery.trim());
  }, [open, initialQuery]);
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
      description="확인한 사실만 근거로 셉니다. 확인 전인 것은 브레인에서 먼저 확인합니다."
    >
      <form
        role="search"
        className="mb-4 flex gap-2"
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
          aria-label="확인한 사실 검색어"
          maxLength={500}
          autoFocus
        />
        <IconButton type="submit" variant="primary" label="검색">
          <SearchIcon size={16} />
        </IconButton>
      </form>
      {!q ? (
        <p className="text-sm text-muted">문단 내용과 관련된 낱말로 확인한 사실을 찾습니다.</p>
      ) : (
        <Loaded query={result}>
          {(r) =>
            r.hits.length ? (
              <ul className="divide-y divide-line border-t border-line">
                {r.hits.map((h: SearchHit) => {
                  const on = selected.includes(h.id);
                  const confirmed = CONFIRMED.includes(h.claim_status as ClaimStatus);
                  return (
                    <li key={h.id} className="flex items-start gap-3 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm leading-6">{h.snippet}</p>
                        <p className="mt-0.5 truncate text-[13px] text-muted">
                          {[
                            h.claim_status
                              ? (STATUS_LABEL[h.claim_status as VerificationStatus] ?? h.claim_status)
                              : null,
                            h.experience_title,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                        {!confirmed ? (
                          <p className="mt-0.5 text-[13px] font-medium">확인 전이라 근거로 세지 않습니다.</p>
                        ) : null}
                      </div>
                      <Button
                        size="sm"
                        className="max-sm:h-9"
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
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="border-t border-line pt-3 text-sm text-muted">
                찾은 사실이 없습니다. 다른 낱말로 찾아 봅니다.
              </p>
            )
          }
        </Loaded>
      )}
    </Sheet>
  );
}
