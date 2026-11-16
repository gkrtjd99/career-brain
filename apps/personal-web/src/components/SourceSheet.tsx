import { Highlight, Sheet } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../lib/api";
import { Loaded } from "./common";

export interface SourceTarget {
  parseRunId: string;
  blockId: string;
  sourceTitle?: string | null;
  page?: number | null;
  matchStart?: number | null;
  matchEnd?: number | null;
}

const CONTEXT = 2;

/** 원본 보기: 검색에 걸린 원문 블록을 앞뒤 블록과 함께 보여주고, 일치한 낱말을 강조한다. */
export function SourceSheet({ target, onClose }: { target: SourceTarget | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["blocks", target?.parseRunId],
    queryFn: () => api.blocks(target!.parseRunId),
    enabled: !!target,
    staleTime: Infinity,
  });
  const where = target
    ? [target.sourceTitle, target.page ? `${target.page}쪽` : null].filter(Boolean).join(" · ")
    : undefined;
  const shown = useMemo(() => {
    if (!q.data || !target) return [];
    const i = q.data.findIndex((b) => b.id === target.blockId);
    if (i < 0) return [];
    return q.data.slice(Math.max(0, i - CONTEXT), i + CONTEXT + 1);
  }, [q.data, target]);

  return (
    <Sheet open={!!target} onOpenChange={(o) => !o && onClose()} title="원본 보기" description={where || undefined} width="lg">
      <Loaded query={q}>
        {(blocks) =>
          shown.length ? (
            <div className="space-y-3">
              {shown.map((b) =>
                b.id === target!.blockId ? (
                  <div key={b.id} className="rounded-md border border-accent/40 bg-accent-soft/40 p-2.5">
                    <p className="mb-1 text-xs text-muted">
                      블록 {b.ordinal + 1}
                      {b.page ? ` · ${b.page}쪽` : ""} · 검색 결과
                    </p>
                    <Highlight
                      text={b.text}
                      start={target!.matchStart ?? -1}
                      end={target!.matchEnd ?? -1}
                    />
                  </div>
                ) : (
                  <div key={b.id} className="px-2.5 text-muted">
                    <p className="mb-1 text-xs">
                      블록 {b.ordinal + 1}
                      {b.page ? ` · ${b.page}쪽` : ""}
                    </p>
                    <p className="text-sm leading-7 break-words whitespace-pre-wrap">{b.text}</p>
                  </div>
                ),
              )}
              <p className="border-t border-line pt-3 text-xs text-muted">
                전체 {blocks.length}개 블록 중 일부. 원본은 읽은 그대로이며 고치지 않습니다.
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted">이 블록을 찾을 수 없습니다. 원본을 다시 읽었을 수 있습니다.</p>
          )
        }
      </Loaded>
    </Sheet>
  );
}
