import type { Experience, ExperienceReferences, ExperienceSummary } from "@career-brain/client";
import {
  Button,
  Checkbox,
  ConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Input,
  MoreMenu,
  Sheet,
  Spinner,
  toast,
} from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { EXPERIENCE_KIND_LABEL, periodLabel } from "../../lib/experience";
import { ErrorText } from "../common";

const REFERENCE_LABEL: Record<keyof ExperienceReferences, string> = {
  children: "안의 경험",
  portfolio_items: "포트폴리오 항목",
  interview_sessions: "인터뷰",
  claims_in_use: "다른 곳에서 쓰는 사실",
  resumes: "이력서",
  application_preps: "지원서 준비",
  confirmed_claims: "확인한 사실",
};

function invalidateBrain(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ["experiences"] });
  void qc.invalidateQueries({ queryKey: ["experience"] });
  void qc.invalidateQueries({ queryKey: ["skills"] });
  void qc.invalidateQueries({ queryKey: ["resumes"] });
}

/** 추출이 "같은 경험일 수 있음"으로 표시한 경우: 합치거나, 다른 경험이라고 표시를 지운다. */
export function DuplicateHint({ exp }: { exp: Experience }) {
  const qc = useQueryClient();
  const [merging, setMerging] = useState(false);
  const dismiss = useMutation({
    mutationFn: () => api.dismissDuplicateHint(exp.id),
    onSuccess: () => invalidateBrain(qc),
  });
  if (!exp.possible_duplicate_of) return null;
  return (
    <div className="mb-6 rounded-md border border-warn/40 bg-warn-soft px-3 py-2.5 text-[13px]">
      <p>
        <span className="font-semibold">같은 경험일 수 있음</span> ·{" "}
        <Link
          to="/brain/$experienceId"
          params={{ experienceId: exp.possible_duplicate_of }}
          className="underline underline-offset-2"
        >
          {exp.possible_duplicate_title ?? "기존 경험"}
        </Link>
      </p>
      <p className="mt-0.5 text-muted">다른 자료에서 같은 회사·비슷한 이름으로 나왔습니다. 같으면 합치세요.</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" variant="primary" className="max-sm:h-9" onClick={() => setMerging(true)}>
          합치기
        </Button>
        <Button size="sm" className="max-sm:h-9" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>
          다른 경험입니다
        </Button>
      </div>
      <ErrorText error={dismiss.error} className="mt-1.5" />
      {merging ? (
        <MergeSheet
          target={{ id: exp.possible_duplicate_of, title: exp.possible_duplicate_title ?? "기존 경험" }}
          preselected={[exp.id]}
          onClose={() => setMerging(false)}
        />
      ) : null}
    </div>
  );
}

/** 경험 머리의 ⋯ 메뉴: 다른 경험 합쳐 넣기, 삭제 */
export function ExperienceMoreMenu({ exp }: { exp: Experience }) {
  const [merging, setMerging] = useState(false);
  const [deleting, setDeleting] = useState(false);
  return (
    <>
      <MoreMenu
        items={[
          { label: "다른 경험을 여기에 합치기", onSelect: () => setMerging(true) },
          { label: "삭제", danger: true, separated: true, onSelect: () => setDeleting(true) },
        ]}
      />
      {merging ? <MergeSheet target={{ id: exp.id, title: exp.title }} onClose={() => setMerging(false)} /> : null}
      <DeleteExperienceDialog exp={exp} open={deleting} onClose={() => setDeleting(false)} />
    </>
  );
}

/** 고른 경험들을 target에 합친다. target 칸이 이기고 빈 칸만 채운다. 사실·근거·연결은 옮긴다. */
function MergeSheet({
  target,
  preselected = [],
  onClose,
}: {
  target: { id: string; title: string };
  preselected?: string[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [picked, setPicked] = useState<Set<string>>(new Set(preselected));
  const [q, setQ] = useState("");
  const all = useQuery({ queryKey: ["experiences"], queryFn: () => api.experiences() });
  const rows = useMemo(() => {
    const items = (all.data ?? []).filter((e) => e.id !== target.id);
    const needle = q.trim().toLowerCase();
    const shown = needle
      ? items.filter((e) => [e.title, e.organization, e.role].some((v) => v?.toLowerCase().includes(needle)))
      : items;
    // 같은 경험일 수 있다고 표시된 것을 먼저
    return [...shown].sort(
      (a, b) => Number(b.possible_duplicate_of === target.id) - Number(a.possible_duplicate_of === target.id),
    );
  }, [all.data, q, target.id]);
  const merge = useMutation({
    mutationFn: () => api.mergeExperiences(target.id, { source_ids: [...picked] }),
    onSuccess: (r) => {
      invalidateBrain(qc);
      toast(
        [`${r.merged.length}개를 합쳤습니다`, r.claims_moved ? `사실 ${r.claims_moved}개 옮김` : null, r.claims_folded ? `같은 문장 ${r.claims_folded}개는 근거만 합침` : null]
          .filter(Boolean)
          .join(" · "),
      );
      onClose();
      void navigate({ to: "/brain/$experienceId", params: { experienceId: target.id } });
    },
  });
  const toggle = (id: string, on: boolean) =>
    setPicked((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  return (
    <Sheet open onOpenChange={(o) => (o ? null : onClose())} title={`“${target.title}”에 합치기`} width="lg">
      <p className="text-[13px] text-muted">
        고른 경험의 사실·근거·포트폴리오·인터뷰 연결을 이 경험으로 옮깁니다. 이 경험의 칸이 우선하고 빈 칸만 채웁니다. 합친
        경험은 목록에서 사라집니다.
      </p>
      <Input
        className="mt-3"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="이름·회사로 찾기"
        aria-label="경험 찾기"
      />
      {all.isPending ? (
        <div className="py-6 text-center">
          <Spinner />
        </div>
      ) : (
        <ul className="mt-3 max-h-[50dvh] divide-y divide-line overflow-y-auto border-y border-line">
          {rows.map((e) => (
            <MergeRow key={e.id} e={e} targetId={target.id} checked={picked.has(e.id)} onChange={(on) => toggle(e.id, on)} />
          ))}
          {!rows.length ? <li className="py-4 text-[13px] text-muted">합칠 경험이 없습니다.</li> : null}
        </ul>
      )}
      <ErrorText error={merge.error} className="mt-2" />
      <div className="mt-3 flex gap-2">
        <Button
          variant="primary"
          disabled={!picked.size || picked.size > 20 || merge.isPending}
          onClick={() => merge.mutate()}
        >
          {merge.isPending ? <Spinner /> : null}
          {picked.size ? `${picked.size}개 합치기` : "합치기"}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          취소
        </Button>
      </div>
      {picked.size > 20 ? <p className="mt-1.5 text-[13px]">한 번에 20개까지 합칩니다.</p> : null}
    </Sheet>
  );
}

function MergeRow({
  e,
  targetId,
  checked,
  onChange,
}: {
  e: ExperienceSummary;
  targetId: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  const claims = Object.values(e.claim_counts).reduce((a, n) => a + (n ?? 0), 0);
  return (
    <li>
      <label className="flex min-h-11 cursor-pointer items-start gap-3 py-2">
        <Checkbox className="mt-0.5" checked={checked} onCheckedChange={(c) => onChange(c)} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{e.title}</span>
          <span className="block truncate text-[13px] text-muted tabular-nums">
            {[EXPERIENCE_KIND_LABEL[e.kind], e.organization, periodLabel(e), claims ? `사실 ${claims}` : null]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
        {e.possible_duplicate_of === targetId ? (
          <span className="shrink-0 text-xs font-medium text-warn">같은 경험일 수 있음</span>
        ) : null}
      </label>
    </li>
  );
}

/** 삭제: 쓰는 곳이 있으면 서버가 거절하고(409) 어디서 쓰는지 알려 준다. 확인한 사실이 있으면 한 번 더 묻는다. */
function DeleteExperienceDialog({ exp, open, onClose }: { exp: Experience; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const del = useMutation({
    mutationFn: (includeConfirmed: boolean) => api.deleteExperience(exp.id, includeConfirmed),
    onSuccess: (r) => {
      invalidateBrain(qc);
      toast(r.claims_deleted ? `삭제했습니다 · 사실 ${r.claims_deleted}개 함께 삭제` : "삭제했습니다");
      onClose();
      void navigate({ to: "/brain" });
    },
  });
  const refs =
    del.error instanceof ApiError && del.error.status === 409
      ? ((del.error.detail as { references?: ExperienceReferences } | null)?.references ?? null)
      : null;
  const blockers = refs
    ? (Object.entries(refs) as [keyof ExperienceReferences, number][]).filter(([, n]) => n > 0)
    : [];
  const onlyConfirmed = blockers.length > 0 && blockers.every(([k]) => k === "confirmed_claims");
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          del.reset();
          onClose();
        }
      }}
      title="경험 삭제"
      description={`“${exp.title}”과 이 경험의 사실을 삭제합니다. 되돌릴 수 없습니다. 자격·학력 연결은 풀기만 합니다.`}
      tone="danger"
      confirmLabel={onlyConfirmed ? "확인한 사실까지 삭제" : "삭제"}
      busy={del.isPending}
      confirmDisabled={blockers.length > 0 && !onlyConfirmed}
      onConfirm={() => del.mutate(onlyConfirmed)}
    >
      {blockers.length ? (
        <div>
          <p className="font-medium">{onlyConfirmed ? "확인한 사실이 있습니다." : "이 경험을 쓰는 곳이 있어 삭제할 수 없습니다."}</p>
          <ul className="mt-1 space-y-0.5 text-muted tabular-nums">
            {blockers.map(([k, n]) => (
              <li key={k}>
                {REFERENCE_LABEL[k] ?? k} {n}개
              </li>
            ))}
          </ul>
          {!onlyConfirmed ? (
            <p className="mt-1.5 text-muted">겹치는 경험이라면 삭제 대신 다른 경험에 합치세요. 연결이 함께 옮겨집니다.</p>
          ) : null}
        </div>
      ) : (
        <ErrorText error={del.error} />
      )}
    </ConfirmDialog>
  );
}

/** 확인 대기 사실을 한꺼번에 확인. 기여(본인/팀)를 한 번 고르거나, 적힌 대로 (기여 모름은 건너뜀). */
export function ConfirmAllButton({
  experienceId,
  sourceId,
  count,
  knownContribution,
}: {
  /** 경험 하나 또는 자료 하나에서 나온 확인 전 사실 전부 (둘 중 하나) */
  experienceId?: string;
  sourceId?: string;
  count: number;
  /** 본인·팀이 이미 정해진 사실 수. 0이면 "적힌 대로"는 아무것도 확인하지 못하니 보이지 않는다. 모르면 비운다 */
  knownContribution?: number;
}) {
  const qc = useQueryClient();
  const run = useMutation({
    mutationFn: (contribution: "personal" | "team" | "as_is") =>
      api.confirmClaims({ contribution, experience_id: experienceId, source_id: sourceId }),
    onSuccess: (r) => {
      if (experienceId) void qc.invalidateQueries({ queryKey: ["experience", experienceId] });
      if (sourceId) {
        void qc.invalidateQueries({ queryKey: ["source", sourceId] });
        void qc.invalidateQueries({ queryKey: ["sources"] });
        void qc.invalidateQueries({ queryKey: ["experience"] });
      }
      void qc.invalidateQueries({ queryKey: ["experiences"] });
      void qc.invalidateQueries({ queryKey: ["skills"] });
      const skipped = r.skipped.filter((s) => s.reason === "contribution_required").length;
      toast(
        [
          `${r.confirmed.length}개 확인했습니다`,
          skipped ? `본인·팀을 골라야 하는 ${skipped}개는 남겼습니다` : null,
          r.truncated ? "남은 것은 다시 누르세요" : null,
        ]
          .filter(Boolean)
          .join(" · "),
      );
    },
  });
  return (
    <div className="mt-2">
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button size="sm" className="max-sm:h-9" disabled={run.isPending}>
            {run.isPending ? <Spinner /> : null} {count}개 모두 확인 <ChevronDown size={14} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onSelect={() => run.mutate("personal")}>모두 본인이 한 일로 확인</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => run.mutate("team")}>모두 팀이 한 일로 확인</DropdownMenuItem>
          {knownContribution !== 0 ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => run.mutate("as_is")}>
                적힌 대로 확인 ({knownContribution != null ? `${knownContribution}개, ` : ""}본인·팀 모름은 남김)
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <ErrorText error={run.error} className="mt-1.5" />
    </div>
  );
}
