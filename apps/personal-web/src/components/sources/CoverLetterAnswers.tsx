import type { AnswerAssetKind, Block } from "@career-brain/client";
import { ANSWER_ASSET_KINDS } from "@career-brain/client";
import { Button, Checkbox, Input, Select, SelectItem, Sheet, Spinner, toast } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorText } from "../common";
import { api } from "../../lib/api";
import { ANSWER_KIND_LABEL } from "../../lib/brainMe";

// 자기소개서 자료를 공통 답변(지원 동기·장단점 등 지원마다 다시 쓰는 글)으로 옮긴다.
// 자료는 그대로 내 경험 자료로 남는다. 옮긴 글은 공통 답변에서 고쳐 쓴다.

const COVER_TITLE = /자기\s*소개서|자소서|cover\s*letter/i;

const KIND_WORDS: [AnswerAssetKind, RegExp][] = [
  ["motivation", /지원\s*동기|지원한\s*이유|왜\s*.*지원/],
  ["personality", /장\s*단점|장점|단점|성격/],
  ["aspiration", /입사\s*후|포부|비전|목표/],
  ["conflict", /갈등|협업|팀워크/],
  ["failure", /실패|어려움|극복/],
  ["intro", /자기\s*소개|나를\s*소개|성장\s*과정|가치관/],
];

const HEADER_WORDS = /지원\s*동기|성장\s*과정|장\s*단점|성격|입사\s*후|포부|갈등|실패|직무\s*역량|성공\s*경험|가치관|협업|극복/;

export interface CoverSection {
  title: string;
  body: string;
  kind: AnswerAssetKind;
}

/** 글자 수 안내 "(700자 이내)", "(공백 포함 1,000byte)" */
const LIMIT_NOTE = /\s*[(（][^()（）]*(자|byte|바이트)[^()（）]*[)）]\s*$/i;

function headerTitle(line: string): string | null {
  const t = line.trim().replace(LIMIT_NOTE, "");
  if (!t || t.length > 60) return null;
  const bracket = t.match(/^[[【<〈《「]\s*(.+?)\s*[\]】>〉》」]\s*$/);
  if (bracket) return bracket[1]!;
  const numbered = t.match(/^(?:\d{1,2}|[①-⑩])\s*[.)．]?\s+(.{2,})$/);
  if (numbered && (HEADER_WORDS.test(t) || /[?？]$/.test(t))) return numbered[1]!;
  const bullet = t.match(/^[■□●○◆◇▶▷-]\s*(.{2,})$/);
  if (bullet && HEADER_WORDS.test(t)) return bullet[1]!;
  if (t.length <= 30 && HEADER_WORDS.test(t) && !/[.。]$/.test(t)) return t;
  return null;
}

export function guessKind(title: string): AnswerAssetKind {
  return KIND_WORDS.find(([, re]) => re.test(title))?.[0] ?? "other";
}

/** 읽은 글을 문항 제목 줄로 나눈다. 제목이 하나도 없으면 전체를 한 덩어리로. 본문이 짧은 덩어리는 뺀다. */
export function splitCoverLetter(blocks: Block[]): CoverSection[] {
  const lines = blocks.flatMap((b) => b.text.split("\n"));
  const out: { title: string; body: string[] }[] = [];
  let cur: { title: string; body: string[] } | null = null;
  for (const line of lines) {
    const h = headerTitle(line);
    if (h) {
      cur = { title: h.trim(), body: [] };
      out.push(cur);
    } else if (line.trim()) {
      if (!cur) {
        cur = { title: "", body: [] };
        out.push(cur);
      }
      cur.body.push(line.trim());
    }
  }
  return out
    .map((s) => ({ title: s.title, body: s.body.join("\n") }))
    .filter((s) => s.body.length >= 80)
    .map((s) => ({ ...s, title: s.title || "자기소개서", kind: guessKind(s.title) }));
}

export function looksLikeCoverLetter(title: string, filename?: string | null) {
  return COVER_TITLE.test(title) || COVER_TITLE.test(filename ?? "");
}

/** 자료 화면 한 칸: 자기소개서로 보이는 자료에서 공통 답변 만들기 */
export function CoverLetterAnswers({ parseRunId, sourceTitle }: { parseRunId: string; sourceTitle: string }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="space-y-2">
      <h2 className="text-[15px] font-semibold">공통 답변</h2>
      <p className="text-sm text-muted">
        자기소개서로 보입니다. 지원 동기·장단점처럼 지원마다 다시 쓰는 글은 공통 답변으로 옮겨 두면 다음 지원서에 바로 넣을 수
        있습니다. 이 자료는 그대로 남습니다.
      </p>
      <Button size="sm" className="max-sm:h-9" onClick={() => setOpen(true)}>
        공통 답변으로 옮기기
      </Button>
      {open ? <CoverSheet parseRunId={parseRunId} sourceTitle={sourceTitle} onClose={() => setOpen(false)} /> : null}
    </section>
  );
}

function CoverSheet({ parseRunId, sourceTitle, onClose }: { parseRunId: string; sourceTitle: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["blocks", parseRunId], queryFn: () => api.blocks(parseRunId), staleTime: Infinity });
  return (
    <Sheet open onOpenChange={(o) => (o ? null : onClose())} title="공통 답변으로 옮기기" width="lg">
      {q.isPending ? (
        <Spinner />
      ) : q.error ? (
        <ErrorText error={q.error} />
      ) : (
        <CoverForm sections={splitCoverLetter(q.data ?? [])} sourceTitle={sourceTitle} onClose={onClose} />
      )}
    </Sheet>
  );
}

function CoverForm({
  sections,
  sourceTitle,
  onClose,
}: {
  sections: CoverSection[];
  sourceTitle: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [rows, setRows] = useState(() => sections.map((s) => ({ ...s, pick: true })));
  const picked = rows.filter((r) => r.pick && r.title.trim());
  const save = useMutation({
    mutationFn: async () => {
      const ids: string[] = [];
      for (const r of picked)
        ids.push((await api.createAnswerAsset({ kind: r.kind, title: r.title.trim(), body: r.body.slice(0, 20000) })).id);
      return ids;
    },
    onSuccess: (ids) => {
      void qc.invalidateQueries({ queryKey: ["answer-assets"] });
      toast(`공통 답변 ${ids.length}개를 만들었습니다.`, { description: `${sourceTitle}에서 옮김` });
      onClose();
      if (ids.length === 1) void navigate({ to: "/brain/answers/$answerId", params: { answerId: ids[0]! } });
      else void navigate({ to: "/brain/answers" });
    },
  });
  if (!rows.length)
    return <p className="text-sm text-muted">옮길 만한 글을 찾지 못했습니다. 공통 답변 화면에서 직접 붙여넣으세요.</p>;
  const set = (i: number, patch: Partial<(typeof rows)[number]>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">문항 제목으로 나눴습니다. 옮길 글을 고르고 이름과 종류를 확인하세요.</p>
      <ul className="divide-y divide-line border-y border-line">
        {rows.map((r, i) => (
          <li key={i} className="flex gap-3 py-3">
            <Checkbox
              checked={r.pick}
              onCheckedChange={(v) => set(i, { pick: v === true })}
              aria-label={`${r.title} 옮기기`}
              className="mt-2.5"
            />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex flex-wrap gap-2">
                <Input
                  value={r.title}
                  onChange={(e) => set(i, { title: e.target.value })}
                  aria-label="공통 답변 이름"
                  maxLength={200}
                  className="min-w-0 flex-1 basis-48"
                />
                <Select
                  value={r.kind}
                  onValueChange={(v) => set(i, { kind: v as AnswerAssetKind })}
                  aria-label="종류"
                  className="w-36"
                >
                  {ANSWER_ASSET_KINDS.map((k) => (
                    <SelectItem key={k} value={k}>
                      {ANSWER_KIND_LABEL[k]}
                    </SelectItem>
                  ))}
                </Select>
              </div>
              <p className="line-clamp-3 text-[13px] leading-6 text-muted">{r.body}</p>
              <p className="text-xs text-muted tabular-nums">{r.body.length.toLocaleString()}자</p>
            </div>
          </li>
        ))}
      </ul>
      <ErrorText error={save.error} />
      <Button variant="primary" onClick={() => save.mutate()} disabled={!picked.length || save.isPending}>
        {save.isPending ? <Spinner /> : null} {picked.length}개 공통 답변으로 만들기
      </Button>
    </div>
  );
}
