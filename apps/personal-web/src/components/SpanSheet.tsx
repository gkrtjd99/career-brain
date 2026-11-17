import { Highlight, Sheet } from "@career-brain/ui";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Loaded } from "./common";

/** 주장의 원문 근거 위치: 원본 블록 전체에서 인용 부분을 강조한다. */
export function SpanSheet({ spanId, onClose }: { spanId: string | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["span", spanId],
    queryFn: () => api.span(spanId!),
    enabled: !!spanId,
    staleTime: Infinity,
  });
  const where = q.data
    ? [q.data.source_title, q.data.page ? `${q.data.page}쪽` : null, `블록 ${q.data.ordinal + 1}`]
        .filter(Boolean)
        .join(" · ")
    : undefined;
  return (
    <Sheet open={!!spanId} onOpenChange={(o) => !o && onClose()} title="원문 근거" description={where}>
      <Loaded query={q}>
        {(span) => (
          <div className="space-y-3">
            <Highlight text={span.block_text} start={span.start_char} end={span.end_char} />
            <p className="border-t border-line pt-3 text-xs text-muted">파일: {span.original_filename}</p>
          </div>
        )}
      </Loaded>
    </Sheet>
  );
}
