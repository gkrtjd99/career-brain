import type { ExperienceSummary } from "@career-brain/client";
import { Button, Checkbox, Input, Sheet, Spinner, toast } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Tag } from "lucide-react";
import { useMemo, useState } from "react";
import { ErrorText } from "../common";
import { api } from "../../lib/api";

// 자료에서 찾았지만 어느 경험에도 붙지 않은 기술명. 기술은 따로 적지 않고 경험 태그에서 모으므로,
// 쓴 경험을 골라 태그로 단다. 아니면 숨긴다.

export const SUGGESTIONS_KEY = ["extraction-suggestions"] as const;

export function SkillSuggestions() {
  const q = useQuery({ queryKey: SUGGESTIONS_KEY, queryFn: api.extractionSuggestions });
  const qc = useQueryClient();
  const [term, setTerm] = useState<string | null>(null);
  const hide = useMutation({
    mutationFn: (value: string) => api.dismissExtractionSuggestion({ kind: "skill", value }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: SUGGESTIONS_KEY }),
  });
  const skills = q.data?.skills ?? [];
  if (!skills.length) return null;
  return (
    <section className="mt-8">
      <h2 className="text-[15px] font-semibold">
        자료에서 찾은 기술 <span className="font-normal text-muted tabular-nums">{skills.length}</span>
      </h2>
      <p className="mt-0.5 text-[13px] text-muted">
        이력서의 기술 줄에 있었지만 어느 경험에도 붙지 않았습니다. 쓴 경험에 태그로 달면 위 목록에 들어옵니다.
      </p>
      <ul className="mt-2 divide-y divide-line border-y border-line">
        {skills.map((s) => (
          <li key={s} className="flex min-h-11 items-center gap-2 py-1.5">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{s}</span>
            <Button size="sm" className="shrink-0 max-sm:h-9" onClick={() => setTerm(s)}>
              <Tag size={14} /> 경험에 달기
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="shrink-0 max-sm:h-9"
              disabled={hide.isPending && hide.variables === s}
              onClick={() => hide.mutate(s)}
            >
              숨기기
            </Button>
          </li>
        ))}
      </ul>
      <ErrorText error={hide.error} className="mt-1.5" />
      {term ? <TagSheet term={term} onClose={() => setTerm(null)} /> : null}
    </section>
  );
}

function TagSheet({ term, onClose }: { term: string; onClose: () => void }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["experiences", { limit: 200 }], queryFn: () => api.experiences({ limit: 200 }) });
  const [find, setFind] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const rows = useMemo(() => {
    const f = find.trim().toLowerCase();
    const all = (list.data ?? []).filter((e) => !e.tags.some((t) => t.toLowerCase() === term.toLowerCase()));
    return f
      ? all.filter((e) => [e.title, e.organization, e.role].some((x) => x?.toLowerCase().includes(f)))
      : all;
  }, [list.data, find, term]);
  const save = useMutation({
    mutationFn: async () => {
      const chosen = (list.data ?? []).filter((e) => picked.has(e.id));
      for (const e of chosen) await api.updateExperience(e.id, { tags: [...e.tags, term] });
      return chosen.length;
    },
    onSuccess: (n) => {
      void qc.invalidateQueries({ queryKey: ["experiences"] });
      void qc.invalidateQueries({ queryKey: ["experience"] });
      void qc.invalidateQueries({ queryKey: ["skills"] });
      void qc.invalidateQueries({ queryKey: SUGGESTIONS_KEY });
      toast(`경험 ${n}개에 ${term} 태그를 달았습니다.`);
      onClose();
    },
  });
  const toggle = (e: ExperienceSummary) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(e.id)) n.delete(e.id);
      else n.add(e.id);
      return n;
    });
  return (
    <Sheet open onOpenChange={(o) => (o ? null : onClose())} title={`${term} 태그 달기`}>
      <div className="space-y-3">
        <p className="text-sm text-muted">이 기술을 쓴 경험을 고르세요.</p>
        <Input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="경험 찾기" aria-label="경험 찾기" />
        {list.isPending ? (
          <Spinner />
        ) : (
          <ul className="max-h-[50vh] divide-y divide-line overflow-y-auto border-y border-line">
            {rows.map((e) => (
              <li key={e.id}>
                <label className="flex min-h-11 cursor-pointer items-center gap-3 py-2">
                  <Checkbox checked={picked.has(e.id)} onCheckedChange={() => toggle(e)} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{e.title}</span>
                    {e.organization || e.period_text ? (
                      <span className="block truncate text-[13px] text-muted tabular-nums">
                        {[e.organization, e.period_text].filter(Boolean).join(" · ")}
                      </span>
                    ) : null}
                  </span>
                </label>
              </li>
            ))}
            {!rows.length ? <li className="py-3 text-sm text-muted">맞는 경험이 없습니다.</li> : null}
          </ul>
        )}
        <ErrorText error={list.error ?? save.error} />
        <Button variant="primary" disabled={!picked.size || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Spinner /> : null} {picked.size ? `${picked.size}개 경험에 달기` : "경험을 고르세요"}
        </Button>
      </div>
    </Sheet>
  );
}
