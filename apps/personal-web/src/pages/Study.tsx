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
import { Badge, Button, EmptyState, Input, Label, Section, Select, Sheet, StatusBadge } from "@career-brain/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Link2, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { ClaimPicker } from "../components/ClaimPicker";
import { ErrorText, List, Loaded, PageHeader, Tabs } from "../components/common";
import { AttemptForm, AttemptRow, MistakeForm, ReviewCard, useStudyInvalidate } from "../components/study/parts";
import { api } from "../lib/api";
import { formatDate, formatDateTime, hostOf } from "../lib/format";
import {
  ASSESSMENT_KIND,
  ASSESSMENT_STATUS,
  COVERAGE,
  ITEM_KIND,
  ITEM_STATUS,
  REQ_FIELD,
} from "../lib/labels";

const route = getRouteApi("/study");
export type StudyTab = "reviews" | "coverage" | "frequency" | "items" | "assessments" | "mistakes";

export function StudyPage() {
  const { tab = "reviews", posting } = route.useSearch();
  const navigate = useNavigate();
  const due = useQuery({ queryKey: ["due-reviews"], queryFn: () => api.dueReviews({ limit: 100 }) });
  const [reviewed, setReviewed] = useState<string | null>(null);
  const go = (t: StudyTab, p?: string) =>
    navigate({ to: "/study", search: { ...(t === "reviews" ? {} : { tab: t }), ...(p ? { posting: p } : {}) }, replace: true });
  return (
    <div>
      <PageHeader title="학습" subtitle="공고가 요구하는 것 → 학습·시험 준비 → 오답 복습. 학습은 계획이지 경험이 아닙니다." />
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
      {tab === "reviews" ? <TimezoneLine /> : null}
      {tab === "reviews" && reviewed ? <p className="mb-2 text-xs text-ok">{reviewed}</p> : null}
      {tab === "reviews" ? (
        <Loaded query={due}>
          {(rows) =>
            rows.length ? (
              <div className="space-y-2">
                <p className="text-xs text-muted">답을 떠올려 본 뒤 “답 보기”를 누르고, 얼마나 쉬웠는지 고르면 다음 복습 날짜가 정해집니다.</p>
                {rows.map((m) => (
                  <ReviewCard key={m.id} m={m} title={m.study_item_title} onReviewed={setReviewed} />
                ))}
              </div>
            ) : (
              <EmptyState>오늘 복습할 오답이 없습니다.</EmptyState>
            )
          }
        </Loaded>
      ) : null}
      {tab === "coverage" ? <CoverageTab postingId={posting} onPosting={(p) => go("coverage", p)} /> : null}
      {tab === "frequency" ? <FrequencyTab /> : null}
      {tab === "items" ? <ItemsTab /> : null}
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
    <div className="space-y-4">
      <Loaded query={postings}>
        {() =>
          saved.length ? (
            <div className="max-w-md">
              <Label htmlFor="cov-posting">저장한 공고</Label>
              <Select id="cov-posting" value={selected} onChange={(e) => onPosting(e.target.value)}>
                {saved.map((p) => (
                  <option key={p.id} value={p.id}>
                    {[p.company, p.title ?? hostOf(p.canonical_url)].filter(Boolean).join(" · ")}
                  </option>
                ))}
              </Select>
            </div>
          ) : (
            <EmptyState>
              저장(확정)한 공고가 없습니다.{" "}
              <Link to="/postings" className="text-accent hover:underline">
                공고 저장하기
              </Link>
            </EmptyState>
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
    mutationFn: (v: { requirementId: string; claimId: string }) => api.unlinkRequirementClaim(v.requirementId, v.claimId),
    onSuccess: invalidate,
  });

  return (
    <Loaded query={q}>
      {(cov: Coverage) => {
        const reqs = cov.requirements.filter((r) => r.field !== "process");
        const process = cov.requirements.filter((r) => r.field === "process");
        return (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-2">
              {(Object.keys(COVERAGE) as (keyof typeof COVERAGE)[]).map((k) =>
                cov.counts[k] ? (
                  <Badge key={k} tone={COVERAGE[k].tone}>
                    {COVERAGE[k].label} {cov.counts[k]}
                  </Badge>
                ) : null,
              )}
              <Button size="sm" variant="primary" className="ml-auto" disabled={plan.isPending} onClick={() => plan.mutate(undefined)}>
                <Sparkles size={14} /> 준비 항목 만들기
              </Button>
            </div>
            <p className="-mt-4 text-xs text-muted">
              근거 없는 요건마다 학습 항목을, 전형 절차의 시험 단계마다 시험 준비를 만듭니다. 여러 번 눌러도 같은 항목을 다시 만들지 않습니다.
              판정은 사용자가 연결한 주장과 학습 기록뿐이며 합격 가능성이 아닙니다.
            </p>
            {planMsg ? <p className="text-[13px] text-ok">{planMsg}</p> : null}
            <ErrorText error={plan.error ?? link.error ?? unlink.error ?? cleanup.error} />
            {reqs.some((r) => !r.current) ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-subtle/60 px-3 py-2 text-[13px]">
                <span className="min-w-0 flex-1">
                  공고를 다시 확정하면서 빠진 요건 {reqs.filter((r) => !r.current).length}줄이 남아 있습니다. 정리하면 그 요건과 주장 연결만 지우고,
                  학습 항목은 그대로 둡니다.
                </span>
                <Button size="sm" variant="ghost" disabled={cleanup.isPending} onClick={() => cleanup.mutate()}>
                  <Trash2 size={14} /> 빠진 요건 정리
                </Button>
              </div>
            ) : null}
            <Section title="자격·우대 요건">
              {reqs.length ? (
                <ul className="space-y-2">
                  {reqs.map((r, i) => (
                    <RequirementRow
                      key={`${r.field}-${i}`}
                      r={r}
                      onPlan={() => plan.mutate([{ field: r.field, text: r.text }])}
                      onLink={() => setLinkFor(r)}
                      onUnlink={(claimId) => r.requirement_id && unlink.mutate({ requirementId: r.requirement_id, claimId })}
                      busy={plan.isPending || unlink.isPending}
                      suggestOpen={suggestFor === `${r.field}:${r.text}`}
                      onSuggest={() => setSuggestFor(suggestFor === `${r.field}:${r.text}` ? null : `${r.field}:${r.text}`)}
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
                </ul>
              ) : (
                <EmptyState>이 공고에 자격·우대 요건이 없습니다.</EmptyState>
              )}
            </Section>
            {process.length ? (
              <Section title="전형 절차">
                <ul className="space-y-2">
                  {process.map((r, i) => (
                    <li key={i} className="rounded-lg border border-line bg-surface p-3 text-[13px]">
                      <p>{r.text}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        {r.steps.map((s, j) => (
                          <Badge key={j} tone={s.kind ? "accent" : "neutral"}>
                            {s.step}
                            {s.kind ? ` · ${ASSESSMENT_KIND[s.kind]}` : ""}
                          </Badge>
                        ))}
                      </div>
                      {r.assessments.map((a) => (
                        <p key={a.id} className="mt-1 text-xs text-muted">
                          시험 준비: {a.title} · {ASSESSMENT_STATUS[a.status].label}
                          {a.scheduled_at ? ` · ${formatDateTime(a.scheduled_at)}` : ""}
                        </p>
                      ))}
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}
            <ClaimPicker
              open={!!linkFor}
              onClose={() => setLinkFor(null)}
              title="요건에 주장 연결"
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
    <li className="rounded-lg border border-line bg-surface p-3">
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 text-[13px] leading-5">{r.text}</p>
        <Badge tone={c.tone}>{c.label}</Badge>
      </div>
      <p className="mt-0.5 text-xs text-muted">
        {REQ_FIELD[r.field]}
        {!r.current ? " · 지금 공고에는 없는 문장" : ""}
      </p>
      {r.claims.length ? (
        <ul className="mt-2 space-y-1">
          {r.claims.map((cl) => (
            <li key={cl.id} className="flex items-start gap-1.5 text-[13px]">
              <StatusBadge status={cl.status} />
              <span className="min-w-0 flex-1">{cl.statement}</span>
              <button className="rounded p-0.5 text-muted hover:text-danger" aria-label="연결 끊기" onClick={() => onUnlink(cl.id)} disabled={busy}>
                <X size={13} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {r.items.length ? (
        <ul className="mt-2 space-y-0.5 text-[13px]">
          {r.items.map((it) => (
            <li key={it.id}>
              <Link to="/study/items/$itemId" params={{ itemId: it.id }} className="inline-flex items-center gap-1.5 hover:underline">
                <Badge tone={ITEM_STATUS[it.status].tone}>{ITEM_STATUS[it.status].label}</Badge>
                {it.title}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button size="sm" variant="ghost" onClick={onLink}>
          <Link2 size={14} /> 주장 연결
        </Button>
        {r.current ? (
          <Button size="sm" variant="ghost" aria-expanded={suggestOpen} onClick={onSuggest}>
            <Search size={14} /> 후보 찾기
          </Button>
        ) : null}
        {!r.items.length && r.coverage !== "evidenced" ? (
          <Button size="sm" variant="ghost" onClick={onPlan} disabled={busy}>
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
  const q = useQuery({
    queryKey: ["claim-suggestions", postingId, r.field, r.text, r.claims.length],
    queryFn: () => api.claimSuggestions(postingId, { field: r.field, text: r.text, limit: 5 }),
  });
  return (
    <div className="mt-2 rounded-md border border-dashed border-line px-2.5 py-2">
      <Loaded query={q}>
        {(res: ClaimSuggestions) => {
          const line = res.lines[0];
          return (
            <div className="space-y-1.5">
              <p className="text-xs text-muted">
                검색 제안 ({res.mode === "hybrid" ? "키워드 + 의미" : "키워드"}, 검색어 “{line?.query}”). 맞는 주장만 직접 연결하세요. 제안만으로는
                연결되거나 충족으로 바뀌지 않습니다.
              </p>
              {line?.candidates.length ? (
                <ul className="space-y-1">
                  {line.candidates.map((c) => (
                    <li key={c.claim_id} className="flex items-start gap-1.5 text-[13px]">
                      <StatusBadge status={c.status} />
                      <span className="min-w-0 flex-1">
                        {c.statement}
                        {c.experience_title ? <span className="text-xs text-muted"> · {c.experience_title}</span> : null}
                      </span>
                      <Button size="sm" variant="ghost" disabled={linking} onClick={() => onLink(c.claim_id)}>
                        <Link2 size={13} /> 연결
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[13px] text-muted">찾은 후보가 없습니다. “주장 연결”에서 직접 검색할 수 있습니다.</p>
              )}
            </div>
          );
        }}
      </Loaded>
    </div>
  );
}

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
        <div className="space-y-3">
          <p className="text-xs text-muted">
            내가 저장한 공고{archived ? "(보관 포함)" : ""}만 센 숫자입니다. 채용 시장 전체 통계가 아닙니다. 기술 낱말은 간단한 규칙으로 묶습니다
            (예: 도커·Docker, k8s·Kubernetes).
          </p>
          <label className="flex items-center gap-1.5 text-[13px]">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> 보관한 공고도 세기
          </label>
          {f.total_postings && f.groups.length ? (
            <List>
              {f.groups.map((g) => (
                <li key={g.key} className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 text-sm">
                      저장 공고 {f.total_postings}개 중 <b className="tabular-nums">{g.postings}개</b>가 <b>{g.label}</b> 요구
                    </span>
                    <span className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-subtle" aria-hidden>
                      <span className="block h-full bg-accent" style={{ width: `${(g.postings / f.total_postings) * 100}%` }} />
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted">
                    {[g.required ? `자격 ${g.required}` : null, g.preferred ? `우대 ${g.preferred}` : null].filter(Boolean).join(" · ")}
                    {g.examples.length ? ` · 예: ${g.examples.join(" / ")}` : ""}
                  </p>
                </li>
              ))}
            </List>
          ) : (
            <EmptyState>저장한 공고의 자격·우대 요건이 없습니다.</EmptyState>
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
    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">
      <span>
        복습 마감: {q.data.timezone} 기준 오늘({q.data.today}) 끝{q.data.is_default ? " (기본값)" : ""}
      </span>
      {editing ? (
        <Select
          aria-label="표시 시간대"
          className="h-7 w-auto text-xs"
          value={q.data.timezone}
          disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.value)}
        >
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
              {z === browser ? " (이 브라우저)" : ""}
            </option>
          ))}
        </Select>
      ) : (
        <button className="text-accent hover:underline" onClick={() => setEditing(true)}>
          시간대 바꾸기
        </button>
      )}
      <ErrorText error={save.error} />
    </div>
  );
}

// ---------------------------------------------------------------- 학습 항목

const ITEM_STATUSES: StudyItemStatus[] = ["planned", "in_progress", "done", "dropped"];

function ItemsTab() {
  const [status, setStatus] = useState<StudyItemStatus | "">("");
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["study-items", status], queryFn: () => api.studyItems(status || undefined) });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="상태" value={status} onChange={(e) => setStatus(e.target.value as StudyItemStatus | "")} className="w-auto">
          <option value="">전체</option>
          {ITEM_STATUSES.map((s) => (
            <option key={s} value={s}>
              {ITEM_STATUS[s].label}
            </option>
          ))}
        </Select>
        <Button size="sm" variant="primary" className="ml-auto" onClick={() => setCreating(true)}>
          <Plus size={14} /> 학습 항목
        </Button>
      </div>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <List>
              {rows.map((it) => (
                <li key={it.id}>
                  <Link to="/study/items/$itemId" params={{ itemId: it.id }} className="flex items-center gap-3 px-3 py-2.5 hover:bg-subtle/60">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{it.title}</div>
                      <div className="truncate text-xs text-muted">
                        {[ITEM_KIND[it.kind], it.requirement?.posting_title, it.total_minutes ? `${it.total_minutes}분` : null]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </div>
                    {it.open_mistakes ? <Badge tone="warn">오답 {it.open_mistakes}</Badge> : null}
                    <Badge tone={ITEM_STATUS[it.status].tone}>{ITEM_STATUS[it.status].label}</Badge>
                    <ChevronRight size={16} className="shrink-0 text-muted" />
                  </Link>
                </li>
              ))}
            </List>
          ) : (
            <EmptyState>학습 항목이 없습니다. 공고별 충족에서 만들거나 직접 추가하세요.</EmptyState>
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
          <Input id="ni-title" value={title} maxLength={300} placeholder="예: Kubernetes 기본 운영" onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="ni-kind">종류</Label>
          <Select id="ni-kind" value={kind} onChange={(e) => setKind(e.target.value as StudyItemKind)}>
            {(Object.keys(ITEM_KIND) as StudyItemKind[]).map((k) => (
              <option key={k} value={k}>
                {ITEM_KIND[k]}
              </option>
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
    <div className="space-y-3">
      <div className="flex">
        <Button size="sm" variant="primary" className="ml-auto" onClick={() => setCreating(true)}>
          <Plus size={14} /> 시험 준비
        </Button>
      </div>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <List>
              {rows.map((a) => (
                <li key={a.id}>
                  <button onClick={() => setOpenId(a.id)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-subtle/60">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{a.title}</div>
                      <div className="truncate text-xs text-muted">
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
                    {a.mistakes.filter((m) => m.status === "open").length ? (
                      <Badge tone="warn">오답 {a.mistakes.filter((m) => m.status === "open").length}</Badge>
                    ) : null}
                    <Badge tone={ASSESSMENT_STATUS[a.status].tone}>{ASSESSMENT_STATUS[a.status].label}</Badge>
                    <ChevronRight size={16} className="shrink-0 text-muted" />
                  </button>
                </li>
              ))}
            </List>
          ) : (
            <EmptyState>시험 준비 항목이 없습니다. 공고의 전형 절차에서 만들거나 직접 추가하세요.</EmptyState>
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
        scheduled_at: form.when ? new Date(form.when).toISOString() : null,
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
          <Input id="na-title" value={form.title} maxLength={300} placeholder="예: 가상회사 코딩테스트" onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <Label htmlFor="na-kind">종류</Label>
            <Select id="na-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as AssessmentKind })}>
              {(Object.keys(ASSESSMENT_KIND) as AssessmentKind[]).map((k) => (
                <option key={k} value={k}>
                  {ASSESSMENT_KIND[k]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="na-when">일정 (선택)</Label>
            <Input id="na-when" type="datetime-local" value={form.when} onChange={(e) => setForm({ ...form, when: e.target.value })} />
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
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
        <Badge tone={ASSESSMENT_STATUS[a.status].tone}>{ASSESSMENT_STATUS[a.status].label}</Badge>
        <span className="text-muted">{ASSESSMENT_KIND[a.kind]}</span>
        {a.scheduled_at ? <span className="text-muted">· {formatDateTime(a.scheduled_at)}</span> : null}
        <span className="ml-auto flex gap-1">
          {a.status !== "done" ? (
            <Button size="sm" onClick={() => status.mutate("done")} disabled={status.isPending}>
              마침
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => status.mutate("planned")} disabled={status.isPending}>
              예정으로
            </Button>
          )}
          {a.status === "planned" ? (
            <Button size="sm" variant="ghost" onClick={() => status.mutate("cancelled")} disabled={status.isPending}>
              취소
            </Button>
          ) : null}
        </span>
      </div>
      {a.requirement ? (
        <p className="text-xs text-muted">
          {a.requirement.posting_title} · {REQ_FIELD[a.requirement.field]}: {a.requirement.text}
        </p>
      ) : null}
      <ErrorText error={status.error} />
      <Section title={`응시 ${a.attempts.length}회`}>
        {a.attempts.length ? (
          <ul className="space-y-2">
            {a.attempts.map((at) => (
              <li key={at.id} className="rounded-md border border-line px-2.5 py-2">
                <AttemptRow a={at} />
                {mistakeFor === at.id ? (
                  <div className="mt-2">
                    <MistakeForm attemptId={at.id} onDone={() => setMistakeFor(null)} />
                  </div>
                ) : (
                  <Button size="sm" variant="ghost" className="mt-1" onClick={() => setMistakeFor(at.id)}>
                    <Plus size={14} /> 이 응시의 오답
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-muted">아직 응시 기록이 없습니다.</p>
        )}
        <AttemptForm assessmentId={a.id} />
      </Section>
      <Section title={`오답 ${a.mistakes.length}`}>
        {a.mistakes.length ? (
          <div className="space-y-2">
            {a.mistakes.map((m) => (
              <ReviewCard key={m.id} m={m} showGrade={false} />
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-muted">오답이 없습니다.</p>
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
    <div className="space-y-3">
      <Select aria-label="상태" value={status} onChange={(e) => setStatus(e.target.value as typeof status)} className="w-auto">
        <option value="open">복습 중</option>
        <option value="resolved">다 익힘</option>
        <option value="">전체</option>
      </Select>
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <div className="space-y-2">
              {rows.map((m) => (
                <ReviewCard key={m.id} m={m} title={m.study_item_title} showGrade={false} />
              ))}
              <p className="text-xs text-muted">복습 날짜가 된 오답은 “오늘 복습”에 나옵니다 (가장 이른 날짜 {formatDate(rows[0]!.due_at)}).</p>
            </div>
          ) : (
            <EmptyState>오답이 없습니다. 학습 항목이나 시험 응시에서 적습니다.</EmptyState>
          )
        }
      </Loaded>
    </div>
  );
}
