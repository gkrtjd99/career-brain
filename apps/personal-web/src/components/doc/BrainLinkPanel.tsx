import type { BrainBlockMatch } from "@career-brain/client";
import { Button } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ErrorText } from "../common";
import { api } from "../../lib/api";

// 지원 문서 ↔ 브레인 (2026-11-19). 써 둔 글을 브레인에 넣으면 경험·사실 후보를 뽑고, 브레인에서 확인한 사실은
// 원문 인용이 들어 있는 문단에 바로 연결한다. 연결은 편집 중인 글에 더해지고 사람이 저장한다 (자동 저장 없음).

const WAIT_MS = 4000;
const WAIT_LIMIT = 45; // 3분 남짓: 후보 뽑기는 보통 1~2분

export function BrainLinkPanel({
  appId,
  docId,
  revision,
  dirty,
  onJump,
  blockNo,
  onLink,
}: {
  appId: string | null;
  docId: string;
  revision: number;
  dirty: boolean;
  onJump: (blockId: string) => void;
  blockNo: (blockId: string) => number | null;
  /** 편집 중인 글의 그 문단에 주장을 더한다 */
  onLink: (links: { blockId: string; claimIds: string[] }[]) => void;
}) {
  const qc = useQueryClient();
  const [waits, setWaits] = useState(0);
  const matches = useQuery({
    queryKey: ["brain-matches", docId, revision],
    queryFn: () => api.brainMatches(appId, docId),
    // 막 넣었으면 후보가 나올 때까지 잠깐씩 다시 본다
    refetchInterval: (q) => {
      const d = q.state.data;
      return waits > 0 && waits < WAIT_LIMIT && d && !d.confirmed_total && !d.pending_total ? WAIT_MS : false;
    },
  });
  useEffect(() => {
    if (waits > 0 && matches.dataUpdatedAt) setWaits((n) => (n > 0 && n < WAIT_LIMIT ? n + 1 : n));
  }, [matches.dataUpdatedAt]); // eslint-disable-line react-hooks/exhaustive-deps
  const send = useMutation({
    mutationFn: () => api.documentToBrain(appId, docId),
    onSuccess: () => {
      setWaits(1);
      qc.invalidateQueries({ queryKey: ["brain-matches", docId] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
  const [linked, setLinked] = useState<Set<string>>(new Set());
  useEffect(() => setLinked(new Set()), [revision]);

  const m = matches.data;
  const open: BrainBlockMatch[] = (m?.blocks ?? []).filter((b) => b.confirmed.length && !linked.has(b.block_id));
  const sourceId = m?.review_source_id ?? send.data?.source_id ?? m?.source_id ?? null;
  const extracting = waits > 0 && waits < WAIT_LIMIT && m && !m.confirmed_total && !m.pending_total;

  const link = (rows: BrainBlockMatch[]) => {
    onLink(rows.map((r) => ({ blockId: r.block_id, claimIds: r.confirmed.map((c) => c.claim_id) })));
    setLinked((s) => new Set([...s, ...rows.map((r) => r.block_id)]));
  };

  return (
    <section className="mt-8 space-y-2">
      <h3 className="text-sm font-medium">브레인 근거</h3>
      {/* 같은 글이 다른 파일로 이미 브레인에 있으면(문단과 같은 사실이 있으면) 다시 넣자고 하지 않는다 */}
      {m && !m.in_brain && !m.confirmed_total && !m.pending_total ? (
        <>
          <p className="text-[13px] leading-5 text-muted">
            이 글은 아직 브레인에 없습니다. 넣으면 글에서 경험과 사실 후보를 뽑습니다. 브레인에서 확인한 사실은 여기서
            원래 문단에 바로 연결할 수 있습니다.{dirty ? " 저장한 글로 넣습니다." : ""}
          </p>
          <Button size="sm" disabled={send.isPending} onClick={() => send.mutate()}>
            {send.isPending ? "넣는 중" : "이 글을 브레인에 넣기"}
          </Button>
        </>
      ) : null}
      <ErrorText error={send.error ?? matches.error} />
      {extracting ? (
        <p className="text-xs text-muted" role="status">
          넣었습니다. 경험·사실 후보를 뽑는 중입니다 (보통 1~2분).
        </p>
      ) : null}
      {m && m.pending_total ? (
        <p className="text-xs text-muted">
          확인 전 사실 {m.pending_total}개가 이 글의 문단과 같습니다.{" "}
          {sourceId ? (
            <Link
              to="/brain/sources/$sourceId"
              params={{ sourceId }}
              className="inline-flex min-h-9 items-center underline underline-offset-2 hover:text-fg"
            >
              브레인에서 확인하기
            </Link>
          ) : (
            "브레인에서 확인하면 여기서 연결할 수 있습니다."
          )}
        </p>
      ) : null}
      {open.length ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs text-muted">확인한 사실을 연결할 수 있는 문단 {open.length}개</p>
            {open.length > 1 ? (
              <Button size="sm" onClick={() => link(open)}>
                모두 연결
              </Button>
            ) : null}
          </div>
          <ul className="divide-y divide-line border-t border-line">
            {open.map((b) => {
              const no = blockNo(b.block_id);
              return (
                <li key={b.block_id} className="py-2.5 text-[13px]">
                  <div className="flex items-center justify-between gap-2">
                    {no === null ? (
                      <span className="text-xs text-muted">지운 문단</span>
                    ) : (
                      <button
                        type="button"
                        className="min-h-9 text-xs text-muted underline underline-offset-2 hover:text-fg"
                        onClick={() => onJump(b.block_id)}
                      >
                        문단 {no + 1}
                      </button>
                    )}
                    <Button size="sm" disabled={no === null} onClick={() => link([b])}>
                      연결
                    </Button>
                  </div>
                  <ul className="mt-1 space-y-1">
                    {b.confirmed.map((c) => (
                      <li key={c.claim_id} className="leading-5">
                        {c.statement} <span className="text-xs text-muted">· {c.experience_title}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
      {linked.size ? (
        <p className="text-xs text-muted" role="status">
          문단 {linked.size}개에 연결했습니다. 저장하면 반영됩니다.
        </p>
      ) : null}
      {m && (m.in_brain || m.blocks.length) && !extracting && !m.pending_total && !open.length && !linked.size ? (
        <p className="text-xs text-muted">연결할 새 브레인 사실이 없습니다.</p>
      ) : null}
    </section>
  );
}
