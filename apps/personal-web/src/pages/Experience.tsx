import type { Claim, ClaimKind, Contribution, Experience } from "@career-brain/client";
import { Button, cn, Label, MarkdownView, Select, Sheet, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Copy, Download, Pencil, Plus } from "lucide-react";
import { useState, type ReactNode } from "react";
import { ClaimItem } from "../components/ClaimItem";
import { ErrorText, Loaded, PageHeader, Tabs } from "../components/common";
import {
  CreateExperienceForm,
  EMPTY_EXPERIENCE,
  ExperienceForm,
  formValues,
  toBody,
  type ExperienceFormValues,
} from "../components/ExperienceForm";
import { InterviewPanel } from "../components/InterviewPanel";
import { SpanSheet } from "../components/SpanSheet";
import { api } from "../lib/api";
import { EXPERIENCE_KIND_LABEL, PARENT_KINDS, periodLabel } from "../lib/experience";
import { KIND_LABEL, ORIGIN_LABEL } from "../lib/labels";
import type { ExperienceTab } from "../router";

const route = getRouteApi("/brain/$experienceId");

export function ExperiencePage() {
  const { experienceId } = route.useParams();
  const { tab = "claims" } = route.useSearch();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["experience", experienceId],
    queryFn: () => api.experience(experienceId),
  });
  const setTab = (t: ExperienceTab) =>
    navigate({
      to: "/brain/$experienceId",
      params: { experienceId },
      search: t === "claims" ? {} : { tab: t },
      replace: true,
    });

  return (
    <Loaded query={q}>
      {(exp) => {
        const pending = exp.claims.filter((c) => c.status === "extracted").length;
        return (
          <div>
            <PageHeader
              back={{ to: "/brain", label: "브레인" }}
              title={exp.title}
              subtitle={
                <span className="tabular-nums">
                  {[EXPERIENCE_KIND_LABEL[exp.kind], exp.organization, exp.role, ORIGIN_LABEL[exp.origin]]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              }
            />
            <ExperienceInfo exp={exp} />
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                {
                  value: "claims",
                  label: (
                    <span>
                      주장
                      {pending ? (
                        <span className="ml-1.5 font-semibold tabular-nums">
                          <span className="sr-only">확인 필요 </span>
                          {pending}
                        </span>
                      ) : null}
                    </span>
                  ),
                },
                { value: "wiki", label: "위키" },
                { value: "interview", label: "인터뷰" },
              ]}
            />
            {tab === "claims" ? <ClaimsTab exp={exp} /> : null}
            {tab === "wiki" ? <WikiTab experienceId={exp.id} title={exp.title} /> : null}
            {tab === "interview" ? <InterviewPanel experienceId={exp.id} /> : null}
          </div>
        );
      }}
    </Loaded>
  );
}

/** 구조(종류·상위·기간·태그)와 안의 경험. 고치기는 옆 패널에서. */
function ExperienceInfo({ exp }: { exp: Experience }) {
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const period = periodLabel({ ...exp, period_text: null });
  const canHaveChildren = !exp.parent_id && PARENT_KINDS.includes(exp.kind);
  return (
    <section className="mb-8">
      <dl className="divide-y divide-line border-y border-line text-sm">
        <InfoRow label="상위 경험">
          {exp.parent_id ? (
            <Link
              to="/brain/$experienceId"
              params={{ experienceId: exp.parent_id }}
              className="underline underline-offset-2"
            >
              {exp.parent_title}
            </Link>
          ) : (
            <span className="text-muted">없음 (맨 위)</span>
          )}
        </InfoRow>
        <InfoRow label="기간">
          <span className="tabular-nums">{period ?? <span className="text-muted">모름</span>}</span>
          {exp.period_text ? (
            <span className="text-[13px] text-muted"> · 원문 기간: {exp.period_text}</span>
          ) : null}
        </InfoRow>
        <InfoRow label="태그">
          {exp.tags.length ? exp.tags.join(" · ") : <span className="text-muted">없음</span>}
        </InfoRow>
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" className="max-sm:h-9" onClick={() => setEditing(true)}>
          <Pencil size={14} /> 고치기
        </Button>
        {canHaveChildren ? (
          <Button size="sm" className="max-sm:h-9" onClick={() => setAdding(true)}>
            <Plus size={14} /> 안에 프로젝트 추가
          </Button>
        ) : null}
      </div>

      {exp.children.length ? (
        <div className="mt-6">
          <h2 className="text-[15px] font-semibold">
            안의 경험 <span className="font-normal text-muted tabular-nums">{exp.children.length}</span>
          </h2>
          <ul className="mt-2 divide-y divide-line border-t border-line">
            {exp.children.map((c) => (
              <li key={c.id}>
                <Link
                  to="/brain/$experienceId"
                  params={{ experienceId: c.id }}
                  className="-mx-2 flex items-center gap-4 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60"
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{c.title}</span>
                  <span className="shrink-0 text-[13px] text-muted tabular-nums">
                    {[EXPERIENCE_KIND_LABEL[c.kind], periodLabel(c)].filter(Boolean).join(" · ")}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Sheet open={editing} onOpenChange={(o) => !o && setEditing(false)} title="경험 고치기">
        {editing ? <EditExperienceForm exp={exp} onDone={() => setEditing(false)} /> : null}
      </Sheet>
      <Sheet open={adding} onOpenChange={(o) => !o && setAdding(false)} title="안에 프로젝트 추가">
        {adding ? (
          <CreateExperienceForm
            initial={{
              ...EMPTY_EXPERIENCE,
              kind: "project",
              parent_id: exp.id,
              organization: exp.organization ?? "",
            }}
            onDone={() => setAdding(false)}
          />
        ) : null}
      </Sheet>
    </section>
  );
}

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-4 py-2.5">
      <dt className="w-20 shrink-0 text-[13px] text-muted">{label}</dt>
      <dd className="min-w-0 flex-1 break-words">{children}</dd>
    </div>
  );
}

function EditExperienceForm({ exp, onDone }: { exp: Experience; onDone: () => void }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (v: ExperienceFormValues) => api.updateExperience(exp.id, toBody(v)),
    onSuccess: (updated) => {
      qc.setQueryData(["experience", exp.id], updated);
      qc.invalidateQueries({ queryKey: ["experiences"] });
      // 상위가 바뀌면 옛·새 상위의 "안의 경험"도 바뀐다
      qc.invalidateQueries({ queryKey: ["experience"] });
      onDone();
    },
  });
  return (
    <ExperienceForm
      initial={formValues(exp)}
      selfId={exp.id}
      hasChildren={exp.children.length > 0}
      submitLabel="저장"
      pending={save.isPending}
      error={save.error}
      onSubmit={(v) => save.mutate(v)}
      onCancel={onDone}
    />
  );
}

function ClaimsTab({ exp }: { exp: Experience }) {
  const [spanId, setSpanId] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const groups: { title: string; hint?: string; claims: Claim[] }[] = [
    {
      title: "확인 대기",
      hint: "AI가 원문에서 뽑은 후보입니다. 사실이면 확인, 다르면 고치거나 ‘사실과 달라요’.",
      claims: exp.claims.filter((c) => c.status === "extracted"),
    },
    {
      title: "확인한 주장",
      claims: exp.claims.filter((c) => c.status === "self_confirmed" || c.status === "evidence_supported"),
    },
    { title: "이견 있음", claims: exp.claims.filter((c) => c.status === "disputed") },
  ];
  const retired = exp.claims.filter((c) => c.status === "retired");

  return (
    <div className="space-y-10">
      {exp.claims.length === 0 ? (
        <p className="text-sm text-muted">아직 주장이 없습니다. 아래에서 직접 추가하거나 인터뷰 탭에서 정리하세요.</p>
      ) : null}
      {groups.map((g) =>
        g.claims.length ? (
          <section key={g.title}>
            <h2 className="text-[15px] font-semibold">
              {g.title} <span className="font-normal text-muted tabular-nums">{g.claims.length}</span>
            </h2>
            {g.hint ? <p className="mt-0.5 text-[13px] text-muted">{g.hint}</p> : null}
            <ClaimList claims={g.claims} experienceId={exp.id} onOpenSpan={setSpanId} />
          </section>
        ) : null,
      )}
      {retired.length ? (
        <section>
          <button
            type="button"
            aria-expanded={showArchived}
            onClick={() => setShowArchived((v) => !v)}
            className="inline-flex min-h-9 items-center text-[13px] text-muted hover:text-fg"
          >
            보관한 주장 {retired.length}개 {showArchived ? "숨기기" : "보기"}
          </button>
          {showArchived ? (
            <ClaimList claims={retired} experienceId={exp.id} onOpenSpan={setSpanId} className="opacity-70" />
          ) : null}
        </section>
      ) : null}
      <AddClaim experienceId={exp.id} />
      <SpanSheet spanId={spanId} onClose={() => setSpanId(null)} />
    </div>
  );
}

function ClaimList({
  claims,
  experienceId,
  onOpenSpan,
  className,
}: {
  claims: Claim[];
  experienceId: string;
  onOpenSpan: (spanId: string) => void;
  className?: string;
}) {
  return (
    <ul className={cn("mt-3 divide-y divide-line border-t border-line", className)}>
      {claims.map((c) => (
        <li key={c.id}>
          <ClaimItem claim={c} experienceId={experienceId} onOpenSpan={onOpenSpan} />
        </li>
      ))}
    </ul>
  );
}

function AddClaim({ experienceId }: { experienceId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [statement, setStatement] = useState("");
  const [kind, setKind] = useState<ClaimKind>("action");
  const [contribution, setContribution] = useState<Contribution>("personal");
  const add = useMutation({
    mutationFn: () => api.addClaim(experienceId, { statement: statement.trim(), kind, contribution }),
    onSuccess: () => {
      setStatement("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["experience", experienceId] });
      qc.invalidateQueries({ queryKey: ["experiences"] });
    },
  });
  if (!open)
    return (
      <Button onClick={() => setOpen(true)}>
        <Plus size={15} /> 주장 직접 추가
      </Button>
    );
  return (
    <section>
      <h2 className="mb-3 text-[15px] font-semibold">주장 직접 추가</h2>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (statement.trim()) add.mutate();
        }}
      >
        <Textarea
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          placeholder="예: 배송 상태 조회 API의 응답 시간을 줄이기 위해 캐시를 도입했다."
          maxLength={4000}
          aria-label="주장 문장"
          autoFocus
        />
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="new-kind">종류</Label>
            <Select id="new-kind" value={kind} onChange={(e) => setKind(e.target.value as ClaimKind)}>
              {(Object.keys(KIND_LABEL) as ClaimKind[]).map((k) => (
                <option key={k} value={k}>
                  {KIND_LABEL[k]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="new-contrib">기여</Label>
            <Select
              id="new-contrib"
              value={contribution}
              onChange={(e) => setContribution(e.target.value as Contribution)}
            >
              <option value="personal">본인</option>
              <option value="team">팀</option>
              <option value="unknown">모름</option>
            </Select>
          </div>
        </div>
        <ErrorText error={add.error} />
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={!statement.trim() || add.isPending}>
            추가
          </Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            취소
          </Button>
        </div>
        <p className="text-[13px] text-muted">직접 쓴 주장은 원문 근거가 없으면 “근거 없음”으로 표시됩니다.</p>
      </form>
    </section>
  );
}

function WikiTab({ experienceId, title }: { experienceId: string; title: string }) {
  const q = useQuery({
    queryKey: ["wiki-md", experienceId],
    queryFn: () => api.wikiMarkdown(experienceId),
  });
  const [copied, setCopied] = useState(false);
  return (
    <Loaded query={q}>
      {(md) => (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[13px] text-muted">확인한 주장만 모은 정리본입니다.</p>
            <div className="flex gap-1.5">
              <Button
                size="sm"
                className="max-sm:h-9"
                onClick={async () => {
                  await navigator.clipboard.writeText(md);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
              >
                <Copy size={14} /> {copied ? "복사함" : "Markdown 복사"}
              </Button>
              <Button
                size="sm"
                className="max-sm:h-9"
                onClick={() => {
                  const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `${title.replace(/[\\/:*?"<>|]/g, "_").slice(0, 80) || "experience"}.md`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                <Download size={14} /> 내려받기
              </Button>
            </div>
          </div>
          <article className="border-t border-line pt-6">
            <MarkdownView source={md} />
          </article>
        </div>
      )}
    </Loaded>
  );
}
