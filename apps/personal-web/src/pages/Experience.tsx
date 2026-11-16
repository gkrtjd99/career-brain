import type { Claim, ClaimKind, Contribution, Experience } from "@career-brain/client";
import { Badge, Button, EmptyState, Label, MarkdownView, Section, Select, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Copy, Download, Plus } from "lucide-react";
import { useState } from "react";
import { ClaimItem } from "../components/ClaimItem";
import { ErrorText, Loaded, PageHeader, Tabs } from "../components/common";
import { InterviewPanel } from "../components/InterviewPanel";
import { SpanSheet } from "../components/SpanSheet";
import { api } from "../lib/api";
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
              subtitle={[exp.organization, exp.role, exp.period_text, ORIGIN_LABEL[exp.origin]]
                .filter(Boolean)
                .join(" · ")}
            />
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                {
                  value: "claims",
                  label: (
                    <span className="inline-flex items-center gap-1.5">
                      주장 {pending ? <Badge tone="warn">{pending}</Badge> : null}
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
    <div className="space-y-7">
      {exp.claims.length === 0 ? <EmptyState>아직 주장이 없습니다. 아래에서 직접 추가하거나 인터뷰로 정리하세요.</EmptyState> : null}
      {groups.map((g) =>
        g.claims.length ? (
          <Section key={g.title} title={`${g.title} ${g.claims.length}`}>
            {g.hint ? <p className="-mt-1 text-xs text-muted">{g.hint}</p> : null}
            <div className="space-y-2">
              {g.claims.map((c) => (
                <ClaimItem key={c.id} claim={c} experienceId={exp.id} onOpenSpan={setSpanId} />
              ))}
            </div>
          </Section>
        ) : null,
      )}
      {retired.length ? (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setShowArchived((v) => !v)}>
            보관한 주장 {retired.length}개 {showArchived ? "숨기기" : "보기"}
          </Button>
          {showArchived ? (
            <div className="mt-2 space-y-2 opacity-80">
              {retired.map((c) => (
                <ClaimItem key={c.id} claim={c} experienceId={exp.id} onOpenSpan={setSpanId} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      <AddClaim experienceId={exp.id} />
      <SpanSheet spanId={spanId} onClose={() => setSpanId(null)} />
    </div>
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
    <Section title="주장 직접 추가">
      <form
        className="space-y-2 rounded-lg border border-line bg-surface p-3"
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
        <p className="text-xs text-muted">
          직접 쓴 주장은 원문 근거가 없으면 “근거 없음”으로 표시됩니다.
        </p>
      </form>
    </Section>
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
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted">확인한 주장만 모은 정리본입니다.</p>
            <div className="flex gap-1.5">
              <Button
                size="sm"
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
          <article className="rounded-lg border border-line bg-surface px-4 py-5 md:px-6">
            <MarkdownView source={md} />
          </article>
        </div>
      )}
    </Loaded>
  );
}
