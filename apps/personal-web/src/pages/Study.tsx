import type {
  Assessment,
  AssessmentKind,
  ClaimSuggestions,
  Coverage,
  RequirementCoverage,
  RequirementField,
  StudyItemKind,
  StudyItemStatus,
} from "@career-brain/client";
import { Button, Checkbox, cn, DateTimeField, FilterChips, Input, Label, Section, Select, SelectItem, Sheet, StatusBadge, Tooltip, TooltipContent, TooltipTrigger } from "@career-brain/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Link2, Plus, Search, Trash2, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { ClaimPicker } from "../components/ClaimPicker";
import { ErrorText, Loaded, PageHeader, Tabs } from "../components/common";
import {
  AttemptForm,
  AttemptRow,
  MistakeForm,
  Quiet,
  ReviewCard,
  Rows,
  TAP,
  useStudyInvalidate,
} from "../components/study/parts";
import { api } from "../lib/api";
import { formatDate, formatDateTime, fromLocalInput } from "../lib/dates";
import { hostOf } from "../lib/format";
import { ASSESSMENT_KIND, ASSESSMENT_STATUS, COVERAGE, ITEM_KIND, ITEM_STATUS, REQ_FIELD } from "../lib/labels";

const route = getRouteApi("/study");
export type StudyTab = "reviews" | "coverage" | "frequency" | "items" | "assessments" | "mistakes";

/** Today의 PlainRow와 같은 행: 제목 줄과 가장자리를 맞추고, 누를 때만 옅게 칠한다. */
const ROW = "-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
/** 밑줄 글자 단추: 좁은 화면 36px, 넓은 화면 24px 높이를 지킨다 */
const TEXT_LINK = "inline-flex min-h-9 items-center text-fg underline underline-offset-2 hover:text-muted sm:min-h-6";

export function StudyPage() {
  const { tab = "reviews", posting } = route.useSearch();
  const navigate = useNavigate();
  const due = useQuery({ queryKey: ["due-reviews"], queryFn: () => api.dueReviews({ limit: 100 }) });
  const [reviewed, setReviewed] = useState<string | null>(null);
  const go = (t: StudyTab, p?: string) =>
    navigate({
      to: "/study",
      search: { ...(t === "reviews" ? {} : { tab: t }), ...(p ? { posting: p } : {}) },
      replace: true,
    });
  return (
    <div>
      <PageHeader title="학습" subtitle="학습 기록은 경험 근거로 쓰지 않습니다." />
      <Tabs<StudyTab>
        value={tab}
        onChange={(t) => go(t, posting)}
        items={[
          { value: "reviews", label: `오늘 복습${due.data?.length ? ` ${due.data.length}` : ""}` },
          { value: "coverage", label: "공고별 충족" },
          { value: "frequency", label: "요건 빈도" },
          { value: "items", label: "학습 항목" },
          { value: "assessments", label: "시험 준비" },
          { value: "mistakes", label: "오답" },
        ]}
      />
      {tab === "reviews" && reviewed ? <p className="mb-3 text-[13px] font-medium">{reviewed}</p> : null}
      {tab === "reviews" ? (
        <Loaded query={due}>
          {(rows) =>
            rows.length ? (
              <div>
                <TimezoneLine />
                <p className="mb-3 text-[13px] text-muted">
                  답을 떠올려 본 뒤 “답 보기”를 누르고, 얼마나 쉬웠는지 고르면 다음 복습 날짜가 정해집니다.
                </p>
                <Rows>
                  {rows.map((m) => (
                    <li key={m.id}>
                      <ReviewCard m={m} title={m.study_item_title} onReviewed={setReviewed} />
                    </li>
                  ))}
                </Rows>
              </div>
            ) : (
              <Quiet plain>
                오늘 복습할 오답이 없습니다.{" "}
                <button type="button" className={TEXT_LINK} onClick={() => go("mistakes")}>
                  오답 모두 보기
                </button>
              </Quiet>
            )
          }
        </Loaded>
      ) : null}
      {tab === "coverage" ? <CoverageTab postingId={posting} onPosting={(p) => go("coverage", p)} /> : null}
      {tab === "frequency" ? <FrequencyTab /> : null}
      {tab === "items" ? <ItemsTab onCoverage={() => go("coverage", posting)} /> : null}
      {tab === "assessments" ? <AssessmentsTab /> : null}
      {tab === "mistakes" ? <MistakesTab /> : null}
    </div>
  );
}

// ---------------------------------------------------------------- 공고별 충족

function CoverageTab({ postingId, onPosting }: { postingId?: string; onPosting: (id: string) => void }) {
  const postings = useQuery({ queryKey: ["postings"], queryFn: api.postings });
  const saved = (postings.data ?? []).filter((p) => p.status === "saved" || p.status === "archived");
  const selected = postingId ?? saved[0]?.id;
  return (
    <div className="space-y-8">
      <Loaded query={postings}>
        {() =>
          saved.length ? (
            <div className="max-w-md">
              <Label htmlFor="cov-posting">저장한 공고</Label>
              <Select id="cov-posting" value={selected ?? ""} onValueChange={(v) => onPosting(v)}>
                {saved.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {[p.company, p.title ?? hostOf(p.canonical_url)].filter(Boolean).join(" · ")}
                  </SelectItem>
                ))}
              </Select>
            </div>
          ) : (
            <Quiet plain>
              저장(확정)한 공고가 없습니다.{" "}
              <Link to="/postings" className={TEXT_LINK}>
                공고 저장하기
              </Link>
            </Quiet>
          )
        }
      </Loaded>
      {selected ? <CoverageView postingId={selected} /> : null}
    </div>
  );
}

function CoverageView({ postingId }: { postingId: string }) {
  const invalidate = useStudyInvalidate();
  const q = useQuery({ queryKey: ["coverage", postingId], queryFn: () => api.coverage(postingId) });
  const [planMsg, setPlanMsg] = useState<string | null>(null);
  const [linkFor, setLinkFor] = useState<RequirementCoverage | null>(null);
  const [suggestFor, setSuggestFor] = useState<string | null>(null);
  const cleanup = useMutation({
    mutationFn: () => api.cleanupRequirements(postingId, { drop_items: false }),
    onSuccess: invalidate,
  });
  const plan = useMutation({
    mutationFn: (lines?: { field: RequirementField; text: string }[]) =>
      api.planFromPosting(postingId, lines ? { lines } : {}),
    onSuccess: (r) => {
      setPlanMsg(
        r.items_created.length || r.assessments_created.length
          ? `학습 항목 ${r.items_created.length}개, 시험 준비 ${r.assessments_created.length}개를 만들었습니다.${r.skipped ? ` 이미 있거나 근거가 있어 건너뛴 줄 ${r.skipped}개.` : ""}`
          : `새로 만들 항목이 없습니다${r.skipped ? ` (건너뛴 줄 ${r.skipped}개)` : ""}.`,
      );
      invalidate();
    },
  });
  const link = useMutation({
    mutationFn: (v: { req: RequirementCoverage; claimId: string }) =>
      api.linkRequirementClaim(postingId, { field: v.req.field, text: v.req.text, claim_id: v.claimId }),
    onSuccess: () => {
      setLinkFor(null);
      invalidate();
    },
  });
  const unlink = useMutation({
    mutationFn: (v: { requirementId: string; claimId: string }) =>
      api.unlinkRequirementClaim(v.requirementId, v.claimId),
    onSuccess: invalidate,
  });

  return (
    <Loaded query={q}>
      {(cov: Coverage) => {
        const reqs = cov.requirements.filter((r) => r.field !== "process");
        const process = cov.requirements.filter((r) => r.field === "process");
        const stale = reqs.filter((r) => !r.current).length;
        const counts = (Object.keys(COVERAGE) as (keyof typeof COVERAGE)[])
          .filter((k) => cov.counts[k])
          .map((k) => `${COVERAGE[k].label} ${cov.counts[k]}`);
        return (
          <div className="space-y-10">
            <div className="space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1 basis-64 space-y-1">
                  {counts.length ? <p className="text-sm font-medium tabular-nums">{counts.join(" · ")}</p> : null}
                  <p className="text-[13px] text-muted">
                    근거 없는 요건마다 학습 항목을, 전형 절차의 시험 단계마다 시험 준비를 만듭니다. 여러 번 눌러도 같은
                    항목을 다시 만들지 않습니다. 판정은 연결한 사실과 학습 기록만 보며 합격 가능성이 아닙니다.
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="primary"
                  className={TAP}
                  disabled={plan.isPending}
                  onClick={() => plan.mutate(undefined)}
                >
                  준비 항목 만들기
                </Button>
              </div>
              {planMsg ? <p className="text-[13px] font-medium">{planMsg}</p> : null}
              <ErrorText error={plan.error ?? link.error ?? unlink.error ?? cleanup.error} />
              {stale ? (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-subtle/60 px-3 py-2 text-[13px]">
                  <span className="min-w-0 flex-1 basis-56">
                    공고를 다시 확정하면서 빠진 요건 {stale}줄이 남아 있습니다. 정리하면 그 요건과 사실 연결만 없애고,
                    학습 항목은 그대로 둡니다.
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className={TAP}
                    disabled={cleanup.isPending}
                    onClick={() => cleanup.mutate()}
                  >
                    <Trash2 size={14} /> 빠진 요건 정리
                  </Button>
                </div>
              ) : null}
            </div>
            <Section title="자격·우대 요건">
              {reqs.length ? (
                <Rows>
                  {reqs.map((r, i) => (
                    <RequirementRow
                      key={`${r.field}-${i}`}
                      r={r}
                      onPlan={() => plan.mutate([{ field: r.field, text: r.text }])}
                      onLink={() => setLinkFor(r)}
                      onUnlink={(claimId) =>
                        r.requirement_id && unlink.mutate({ requirementId: r.requirement_id, claimId })
                      }
                      busy={plan.isPending || unlink.isPending}
                      suggestOpen={suggestFor === `${r.field}:${r.text}`}
                      onSuggest={() =>
                        setSuggestFor(suggestFor === `${r.field}:${r.text}` ? null : `${r.field}:${r.text}`)
                      }
                      suggestions={
                        suggestFor === `${r.field}:${r.text}` && r.current ? (
                          <SuggestionPanel
                            postingId={postingId}
                            r={r}
                            onLink={(claimId) => link.mutate({ req: r, claimId })}
                            linking={link.isPending}
                          />
                        ) : null
                      }
                    />
                  ))}
                </Rows>
              ) : (
                <Quiet>이 공고에 자격·우대 요건이 없습니다.</Quiet>
              )}
            </Section>
            {process.length ? (
              <Section title="전형 절차">
                <Rows>
                  {process.map((r, i) => (
                    <li key={i} className="py-3 text-sm">
                      <p>{r.text}</p>
                      {r.steps.length ? (
                        <p className="mt-1 text-[13px] text-muted">
                          {r.steps.map((s, j) => (
                            <span key={j}>
                              {j ? " → " : ""}
                              <span className={cn(s.kind && "font-medium text-fg")}>
                                {s.step}
                                {s.kind ? ` (${ASSESSMENT_KIND[s.kind]})` : ""}
                              </span>
                            </span>
                          ))}
                        </p>
                      ) : null}
                      {r.assessments.map((a) => (
                        <p key={a.id} className="mt-0.5 text-[13px] text-muted tabular-nums">
                          시험 준비: {a.title} · {ASSESSMENT_STATUS[a.status].label}
                          {a.scheduled_at ? ` · ${formatDateTime(a.scheduled_at)}` : ""}
                        </p>
                      ))}
                    </li>
                  ))}
                </Rows>
              </Section>
            ) : null}
            <ClaimPicker
              open={!!linkFor}
              onClose={() => setLinkFor(null)}
              title="요건에 사실 연결"
              initialQuery=""
              selected={linkFor?.claims.map((c) => c.id) ?? []}
              onPick={(c) => linkFor && link.mutate({ req: linkFor, claimId: c.id })}
            />
          </div>
        );
      }}
    </Loaded>
  );
}

function RequirementRow({
  r,
  onPlan,
  onLink,
  onUnlink,
  busy,
  suggestOpen,
  onSuggest,
  suggestions,
}: {
  r: RequirementCoverage;
  onPlan: () => void;
  onLink: () => void;
  onUnlink: (claimId: string) => void;
  busy: boolean;
  suggestOpen: boolean;
  onSuggest: () => void;
  suggestions: ReactNode;
}) {
  const c = COVERAGE[r.coverage];
  return (
    <li className="py-4">
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-6">{r.text}</p>
          <p className="text-[13px] text-muted">
            {REQ_FIELD[r.field]}
            {!r.current ? " · 지금 공고에는 없는 문장" : ""}
          </p>
        </div>
        <span
          className={cn(
            "shrink-0 pt-0.5 text-[13px]",
            r.coverage === "uncovered" ? "font-semibold" : r.coverage === "evidenced" ? "font-medium" : "text-muted",
          )}
        >
          {c.label}
        </span>
      </div>
      {r.claims.length ? (
        <ul className="mt-2 space-y-1">
          {r.claims.map((cl) => (
            <li key={cl.id} className="flex items-start gap-2 text-[13px]">
              <StatusBadge status={cl.status} />
              <span className="min-w-0 flex-1 pt-0.5">{cl.statement}</span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    className="-my-1.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-danger disabled:opacity-50 max-sm:h-9 max-sm:w-9"
                    aria-label="연결 해제"
                    onClick={() => onUnlink(cl.id)}
                    disabled={busy}
                  >
                    <X size={14} />
                  </button>
                </TooltipTrigger>
                <TooltipContent>연결 해제</TooltipContent>
              </Tooltip>
            </li>
          ))}
        </ul>
      ) : null}
      {r.items.length ? (
        <ul className="mt-2 space-y-0.5 text-[13px]">
          {r.items.map((it) => (
            <li key={it.id}>
              <Link to="/study/items/$itemId" params={{ itemId: it.id }} className="underline-offset-2 hover:underline">
                {it.title}
                <span className="text-muted"> · {ITEM_STATUS[it.status].label}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-2 -ml-2.5 flex flex-wrap gap-1">
        <Button size="sm" variant="ghost" className={TAP} onClick={onLink}>
          <Link2 size={14} /> 사실 연결
        </Button>
        {r.current ? (
          <Button size="sm" variant="ghost" className={TAP} aria-expanded={suggestOpen} onClick={onSuggest}>
            <Search size={14} /> 후보 찾기
          </Button>
        ) : null}
        {!r.items.length && r.coverage !== "evidenced" ? (
          <Button size="sm" variant="ghost" className={TAP} onClick={onPlan} disabled={busy}>
            <Plus size={14} /> 학습 항목
          </Button>
        ) : null}
      </div>
      {suggestions}
    </li>
  );
}

function SuggestionPanel({
  postingId,
  r,
  onLink,
  linking,
}: {
  postingId: string;
  r: RequirementCoverage;
  onLink: (claimId: string) => void;
  linking: boolean;
}) {
  const [judge, setJudge] = useState(false);
  const q = useQuery({
    queryKey: ["claim-suggestions", postingId, r.field, r.text, r.claims.length, judge],
    queryFn: () => api.claimSuggestions(postingId, { field: r.field, text: r.text, limit: 5, judge }),
  });
  return (
    <div className="mt-2 border-t border-line pt-3">
      <Loaded query={q}>
        {(res: ClaimSuggestions) => {
          const line = res.lines[0];
          return (
            <div className="space-y-2">
              <p className="text-[13px] text-muted">
                “{line?.query}”로 찾은 사실입니다. 맞는 것만 연결하세요.
              </p>
              {line?.candidates.length ? (
                <ul className="space-y-1">
                  {line.candidates.map((c) => (
                    <li key={c.claim_id} className="flex items-start gap-2 text-[13px]">
                      <StatusBadge status={c.status} />
                      <span className="min-w-0 flex-1 pt-0.5">
                        {c.statement}
                        {c.experience_title ? <span className="text-muted"> · {c.experience_title}</span> : null}
                        {c.judge ? (
                          <span className="block text-muted">
                            AI 의견: {JUDGE_VERDICT[c.judge.verdict]}
                            {c.judge.reason ? ` — ${c.judge.reason}` : ""}
                          </span>
                        ) : c.judge_note === "restricted_not_sent" ? (
                          <span className="block text-muted">이 기기 밖으로 보내지 않는 자료라 AI에 보내지 않았습니다.</span>
                        ) : null}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        className={TAP}
                        disabled={linking}
                        onClick={() => onLink(c.claim_id)}
                      >
                        <Link2 size={13} /> 연결
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[13px] text-muted">찾은 후보가 없습니다. “사실 연결”에서 직접 검색할 수 있습니다.</p>
              )}
              {res.judge ? (
                <p className="text-[13px] text-muted">
                  AI 의견: {JUDGE_STATUS[res.judge.status] ?? res.judge.status}. 참고 의견이라 연결은 바뀌지 않습니다.
                </p>
              ) : line?.candidates.length ? (
                <Button size="sm" variant="ghost" className={cn(TAP, "-ml-2.5")} onClick={() => setJudge(true)}>
                  AI로 확인
                </Button>
              ) : null}
            </div>
          );
        }}
      </Loaded>
    </div>
  );
}

const JUDGE_VERDICT = { yes: "보여 줌", no: "보여 주지 않음", unclear: "판단 어려움" } as const;
const JUDGE_STATUS: Record<string, string> = {
  ok: "확인함",
  partial: "일부만 확인함",
  unavailable: "쓸 수 없음",
  denied: "거부됨",
  error: "오류",
};

// ---------------------------------------------------------------- 요건 빈도

function FrequencyTab() {
  const [archived, setArchived] = useState(false);
  const q = useQuery({
    queryKey: ["requirement-frequency", archived],
    queryFn: () => api.requirementFrequency({ include_archived: archived }),
  });
  return (
    <Loaded query={q}>
      {(f) => (
        <div className="space-y-4">
          <div className="space-y-1">
            <p className="text-[13px] text-muted">
              내가 저장한 공고{archived ? "(보관 포함)" : ""}만 센 숫자입니다. 채용 시장 전체 통계가 아닙니다. 기술
              낱말은 간단한 규칙과 별칭 사전으로 묶습니다 (예: 도커·Docker, k8s·Kubernetes. 별칭은 설정에서 더할 수
              있습니다).
            </p>
            <label className="inline-flex min-h-9 items-center gap-2 text-[13px]">
              <Checkbox checked={archived} onCheckedChange={(checked) => setArchived(checked)} /> 보관한 공고도
              세기
            </label>
          </div>
          {f.total_postings && f.groups.length ? (
            <Rows>
              {f.groups.map((g) => (
                <li key={g.key} className="py-3">
                  <div className="flex items-center gap-4">
                    <span className="min-w-0 flex-1 text-sm">
                      저장 공고 {f.total_postings}개 중 <b className="font-semibold tabular-nums">{g.postings}개</b>가{" "}
                      <b className="font-semibold">{g.label}</b> 요구
                    </span>
                    <span className="h-1 w-20 shrink-0 overflow-hidden rounded-full bg-subtle sm:w-28" aria-hidden>
                      <span
                        className="block h-full bg-accent"
                        style={{ width: `${(g.postings / f.total_postings) * 100}%` }}
                      />
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-[13px] text-muted tabular-nums">
                    {[g.required ? `자격 ${g.required}` : null, g.preferred ? `우대 ${g.preferred}` : null]
                      .filter(Boolean)
                      .join(" · ")}
                    {g.examples.length ? ` · 예: ${g.examples.join(" / ")}` : ""}
                  </p>
                </li>
              ))}
            </Rows>
          ) : (
            <Quiet>
              저장한 공고의 자격·우대 요건이 없습니다.{" "}
              <Link to="/postings" className={TEXT_LINK}>
                공고 보기
              </Link>
            </Quiet>
          )}
        </div>
      )}
    </Loaded>
  );
}

// ---------------------------------------------------------------- 표시 시간대

const COMMON_ZONES = ["Asia/Seoul", "Asia/Tokyo", "UTC", "Europe/London", "America/New_York", "America/Los_Angeles"];

function TimezoneLine() {
  const invalidate = useStudyInvalidate();
  const q = useQuery({ queryKey: ["study-settings"], queryFn: api.studySettings });
  const [editing, setEditing] = useState(false);
  const save = useMutation({
    mutationFn: (tz: string) => api.putStudySettings(tz),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });
  if (!q.data) return null;
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zones = Array.from(new Set([q.data.timezone, browser, ...COMMON_ZONES].filter(Boolean)));
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted">
      <span>
        오늘 복습은 오늘 밤 자정까지입니다.
        {q.data.timezone !== browser ? " 이 기기와 다른 시간대를 씁니다." : ""}
      </span>
      {editing ? (
        <Select
          aria-label="시간대"
          className="w-auto text-[13px]"
          value={q.data.timezone}
          disabled={save.isPending}
          onValueChange={(v) => save.mutate(v)}
        >
          {zones.map((z) => (
            <SelectItem key={z} value={z}>
              {z}
              {z === browser ? " (이 브라우저)" : ""}
            </SelectItem>
          ))}
        </Select>
      ) : (
        <button type="button" className={TEXT_LINK} onClick={() => setEditing(true)}>
          시간대 바꾸기
        </button>
      )}
      <ErrorText error={save.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 학습 항목

const ITEM_STATUSES: StudyItemStatus[] = ["planned", "in_progress", "done", "dropped"];

function ItemsTab({ onCoverage }: { onCoverage: () => void }) {
  const [status, setStatus] = useState<StudyItemStatus | "">("");
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["study-items", status], queryFn: () => api.studyItems(status || undefined) });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips<StudyItemStatus | "">
          label="상태"
          value={status}
          onChange={setStatus}
          items={[{ value: "", label: "전체" }, ...ITEM_STATUSES.map((s) => ({ value: s, label: ITEM_STATUS[s].label }))]}
        />
        <Button size="sm" variant="primary" className={cn(TAP, "ml-auto")} onClick={() => setCreating(true)}>
          <Plus size={14} /> 학습 항목
        </Button>
      </div>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <Rows>
              {rows.map((it) => (
                <li key={it.id}>
                  <Link to="/study/items/$itemId" params={{ itemId: it.id }} className={ROW}>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{it.title}</div>
                      <div className="line-clamp-2 text-[13px] text-muted tabular-nums">
                        {[
                          ITEM_KIND[it.kind],
                          it.requirement?.posting_title,
                          it.total_minutes ? `${it.total_minutes}분` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </div>
                    <div className="shrink-0 text-right text-[13px] leading-tight">
                      <div className={cn(it.status !== "in_progress" && "text-muted")}>
                        {ITEM_STATUS[it.status].label}
                      </div>
                      {it.open_mistakes ? (
                        <div className="font-semibold tabular-nums">오답 {it.open_mistakes}</div>
                      ) : null}
                    </div>
                  </Link>
                </li>
              ))}
            </Rows>
          ) : (
            <Quiet>
              학습 항목이 없습니다.{" "}
              <button type="button" className={TEXT_LINK} onClick={onCoverage}>
                공고별 충족
              </button>
              에서 만들거나{" "}
              <button type="button" className={TEXT_LINK} onClick={() => setCreating(true)}>
                직접 추가
              </button>
              하세요.
            </Quiet>
          )
        }
      </Loaded>
      <CreateItemSheet open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

function CreateItemSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invalidate = useStudyInvalidate();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<StudyItemKind>("skill");
  const create = useMutation({
    mutationFn: () => api.createStudyItem({ title: title.trim(), kind }),
    onSuccess: (r) => {
      invalidate();
      setTitle("");
      onClose();
      void navigate({ to: "/study/items/$itemId", params: { itemId: r.id } });
    },
  });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="학습 항목 추가">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) create.mutate();
        }}
      >
        <div>
          <Label htmlFor="ni-title">무엇을 공부하나요</Label>
          <Input
            id="ni-title"
            value={title}
            maxLength={300}
            placeholder="예: Kubernetes 기본 운영"
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="ni-kind">종류</Label>
          <Select id="ni-kind" value={kind} onValueChange={(v) => setKind(v as StudyItemKind)}>
            {(Object.keys(ITEM_KIND) as StudyItemKind[]).map((k) => (
              <SelectItem key={k} value={k}>
                {ITEM_KIND[k]}
              </SelectItem>
            ))}
          </Select>
        </div>
        <ErrorText error={create.error} />
        <Button type="submit" variant="primary" disabled={!title.trim() || create.isPending}>
          추가
        </Button>
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------- 시험 준비

function AssessmentsTab() {
  const q = useQuery({ queryKey: ["assessments"], queryFn: api.assessments });
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <div className="flex">
        <Button size="sm" variant="primary" className={cn(TAP, "ml-auto")} onClick={() => setCreating(true)}>
          <Plus size={14} /> 시험 준비
        </Button>
      </div>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <Rows>
              {rows.map((a) => {
                const open = a.mistakes.filter((m) => m.status === "open").length;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => setOpenId(a.id)}
                      className={cn(ROW, "w-[calc(100%+1rem)] text-left")}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{a.title}</div>
                        <div className="line-clamp-2 text-[13px] text-muted tabular-nums">
                          {[
                            ASSESSMENT_KIND[a.kind],
                            a.scheduled_at ? formatDateTime(a.scheduled_at) : null,
                            a.requirement?.posting_title,
                            a.attempts.length ? `응시 ${a.attempts.length}회` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </div>
                      <div className="shrink-0 text-right text-[13px] leading-tight">
                        <div className={cn(a.status !== "planned" && "text-muted")}>
                          {ASSESSMENT_STATUS[a.status].label}
                        </div>
                        {open ? <div className="font-semibold tabular-nums">오답 {open}</div> : null}
                      </div>
                    </button>
                  </li>
                );
              })}
            </Rows>
          ) : (
            <Quiet>
              시험 준비 항목이 없습니다. 공고의 전형 절차에서 만들거나{" "}
              <button type="button" className={TEXT_LINK} onClick={() => setCreating(true)}>
                직접 추가
              </button>
              하세요.
            </Quiet>
          )
        }
      </Loaded>
      <CreateAssessmentSheet open={creating} onClose={() => setCreating(false)} />
      <AssessmentSheet id={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}

function CreateAssessmentSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invalidate = useStudyInvalidate();
  const [form, setForm] = useState({ title: "", kind: "coding_test" as AssessmentKind, when: "" });
  const create = useMutation({
    mutationFn: () =>
      api.createAssessment({
        title: form.title.trim(),
        kind: form.kind,
        scheduled_at: fromLocalInput(form.when),
      }),
    onSuccess: () => {
      invalidate();
      setForm({ title: "", kind: "coding_test", when: "" });
      onClose();
    },
  });
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="시험 준비 추가">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (form.title.trim()) create.mutate();
        }}
      >
        <div>
          <Label htmlFor="na-title">이름</Label>
          <Input
            id="na-title"
            value={form.title}
            maxLength={300}
            placeholder="예: 가상회사 코딩테스트"
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <Label htmlFor="na-kind">종류</Label>
            <Select
              id="na-kind"
              value={form.kind}
              onValueChange={(v) => setForm({ ...form, kind: v as AssessmentKind })}
            >
              {(Object.keys(ASSESSMENT_KIND) as AssessmentKind[]).map((k) => (
                <SelectItem key={k} value={k}>
                  {ASSESSMENT_KIND[k]}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="na-when">일정 (선택)</Label>
            <DateTimeField
              id="na-when"
              value={form.when}
              onChange={(value) => setForm({ ...form, when: value })}
            />
          </div>
        </div>
        <ErrorText error={create.error} />
        <Button type="submit" variant="primary" disabled={!form.title.trim() || create.isPending}>
          추가
        </Button>
      </form>
    </Sheet>
  );
}

function AssessmentSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["assessment", id], queryFn: () => api.assessment(id!), enabled: !!id });
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()} title={q.data?.title ?? "시험 준비"} width="lg">
      <Loaded query={q}>{(a) => <AssessmentBody a={a} />}</Loaded>
    </Sheet>
  );
}

function AssessmentBody({ a }: { a: Assessment }) {
  const invalidate = useStudyInvalidate();
  const [mistakeFor, setMistakeFor] = useState<string | null>(null);
  const status = useMutation({
    mutationFn: (s: Assessment["status"]) => api.patchAssessment(a.id, { status: s }),
    onSuccess: invalidate,
  });
  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <p className="min-w-0 flex-1 text-[13px] text-muted tabular-nums">
            <span className="font-medium text-fg">{ASSESSMENT_STATUS[a.status].label}</span>
            {` · ${ASSESSMENT_KIND[a.kind]}`}
            {a.scheduled_at ? ` · ${formatDateTime(a.scheduled_at)}` : ""}
          </p>
          <span className="flex gap-1">
            {a.status !== "done" ? (
              <Button size="sm" className={TAP} onClick={() => status.mutate("done")} disabled={status.isPending}>
                마침
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                className={TAP}
                onClick={() => status.mutate("planned")}
                disabled={status.isPending}
              >
                예정으로
              </Button>
            )}
            {a.status === "planned" ? (
              <Button
                size="sm"
                variant="ghost"
                className={TAP}
                onClick={() => status.mutate("cancelled")}
                disabled={status.isPending}
              >
                취소
              </Button>
            ) : null}
          </span>
        </div>
        {a.requirement ? (
          <p className="text-[13px] text-muted">
            {a.requirement.posting_title} · {REQ_FIELD[a.requirement.field]}: {a.requirement.text}
          </p>
        ) : null}
        <ErrorText error={status.error} />
      </div>
      <Section title={`응시 ${a.attempts.length}회`}>
        {a.attempts.length ? (
          <Rows>
            {a.attempts.map((at) => (
              <li key={at.id} className="py-3">
                <AttemptRow a={at} />
                {mistakeFor === at.id ? (
                  <div className="mt-3">
                    <MistakeForm attemptId={at.id} onDone={() => setMistakeFor(null)} />
                  </div>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    className={cn(TAP, "mt-1 -ml-2.5")}
                    onClick={() => setMistakeFor(at.id)}
                  >
                    <Plus size={14} /> 이 응시의 오답
                  </Button>
                )}
              </li>
            ))}
          </Rows>
        ) : (
          <Quiet plain>아직 응시 기록이 없습니다. 아래에 적습니다.</Quiet>
        )}
        <div className="mt-4 border-t border-line pt-4">
          <AttemptForm assessmentId={a.id} />
        </div>
      </Section>
      <Section title={`오답 ${a.mistakes.length}`}>
        {a.mistakes.length ? (
          <Rows>
            {a.mistakes.map((m) => (
              <li key={m.id}>
                <ReviewCard m={m} showGrade={false} />
              </li>
            ))}
          </Rows>
        ) : (
          <Quiet>오답이 없습니다. 응시 기록의 “이 응시의 오답”으로 적습니다.</Quiet>
        )}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- 오답

function MistakesTab() {
  const [status, setStatus] = useState<"open" | "resolved" | "">("open");
  const q = useQuery({ queryKey: ["mistakes", status], queryFn: () => api.mistakes(status || undefined) });
  return (
    <div className="space-y-4">
      <FilterChips<"open" | "resolved" | "">
        label="상태"
        value={status}
        onChange={setStatus}
        items={[
          { value: "open", label: "복습 중" },
          { value: "resolved", label: "다 익힘" },
          { value: "", label: "전체" },
        ]}
      />
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <div>
              <Rows>
                {rows.map((m) => (
                  <li key={m.id}>
                    <ReviewCard m={m} title={m.study_item_title} showGrade={false} />
                  </li>
                ))}
              </Rows>
              <p className="mt-4 text-[13px] text-muted tabular-nums">
                복습 날짜가 된 오답은 “오늘 복습”에 나옵니다 (가장 이른 날짜 {formatDate(rows[0]!.due_at)}).
              </p>
            </div>
          ) : (
            <Quiet>
              오답이 없습니다. 학습 항목이나 시험 응시에서 적습니다.{" "}
              <Link to="/study" search={{ tab: "items" }} className={TEXT_LINK}>
                학습 항목 보기
              </Link>
            </Quiet>
          )
        }
      </Loaded>
    </div>
  );
}
