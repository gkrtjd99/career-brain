import type { ApplicationSummary, ResumeKind, ResumeSummary } from "@career-brain/client";
import { Button, Input, Label, Select, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { RESUME_KIND_LABEL, resumeErrorText, TEMPLATE_LABEL } from "../lib/resume";
import { resumesRoute } from "../router";

const ROW =
  "-mx-2 flex min-h-9 items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";

/** 위 판본 → 아래 판본 순서로 펼친 목록 (마스터 → 직무별 → 공고별). 위 판본이 없어진 판본은 맨 아래. */
function tree(rows: ResumeSummary[]): { row: ResumeSummary; depth: number }[] {
  const byParent = new Map<string | null, ResumeSummary[]>();
  const ids = new Set(rows.map((r) => r.id));
  for (const r of rows) {
    const key = r.parent_id && ids.has(r.parent_id) ? r.parent_id : null;
    byParent.set(key, [...(byParent.get(key) ?? []), r]);
  }
  const out: { row: ResumeSummary; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const r of byParent.get(parent) ?? []) {
      out.push({ row: r, depth });
      walk(r.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** 브레인 › 나 › 이력서: 마스터 하나에서 직무별·공고별 판본을 복사해 만든다. 내용은 브레인 기록에서 채운다. */
export function ResumesPage() {
  const { application } = resumesRoute.useSearch();
  const q = useQuery({ queryKey: ["resumes"], queryFn: () => api.resumes() });
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const [making, setMaking] = useState<null | { kind: ResumeKind; parent?: string }>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const createMaster = useMutation({
    mutationFn: () => api.createResume({ kind: "master", fill: true, fill_claims: true }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["resumes"] });
      void navigate({ to: "/brain/resumes/$versionId", params: { versionId: r.id } });
    },
  });
  const appTitle = apps.data?.find((a) => a.id === application)?.title;

  return (
    <div className="max-w-3xl">
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="이력서"
        subtitle="브레인 기록과 확인한 사실로 채우는 이력서입니다. 마스터 하나에서 직무별·공고별 판본을 만듭니다."
        actions={
          q.data?.some((r) => r.kind === "master") ? (
            <Button variant="primary" onClick={() => setMaking({ kind: application ? "posting" : "role" })}>
              <Plus size={15} /> 판본 만들기
            </Button>
          ) : null
        }
      />
      <Loaded query={q}>
        {(rows) => {
          if (!rows.length)
            return (
              <div className="space-y-3 border-t border-line pt-4 text-sm">
                <p className="text-muted">
                  아직 이력서가 없습니다. 마스터 이력서를 만들면 프로필·경험·학력·자격·기술을 브레인에서 가져오고, 경험마다
                  확인한 사실을 성과 줄로 넣습니다. 모델이 글을 새로 쓰지 않습니다.
                </p>
                <Button variant="primary" onClick={() => createMaster.mutate()} disabled={createMaster.isPending}>
                  브레인에서 마스터 이력서 만들기
                </Button>
                {createMaster.error ? (
                  <p role="alert" className="text-[13px] text-danger">
                    {resumeErrorText(createMaster.error) ?? errorText(createMaster.error)}
                  </p>
                ) : null}
              </div>
            );
          const forApp = application ? rows.filter((r) => r.application_id === application) : [];
          return (
            <div className="space-y-8">
              {application ? (
                <section aria-label="이 공고용 이력서">
                  <h2 className="text-[15px] font-semibold">이 공고용 이력서</h2>
                  <p className="text-[13px] text-muted">{appTitle ?? "지원"}에 이은 판본입니다.</p>
                  {forApp.length ? (
                    <ul className="mt-2 divide-y divide-line border-y border-line">
                      {forApp.map((r) => (
                        <VersionRow key={r.id} r={r} depth={0} />
                      ))}
                    </ul>
                  ) : (
                    <div className="mt-2 flex flex-wrap items-center gap-3 border-t border-line pt-3 text-sm">
                      <span className="text-muted">아직 없습니다. 마스터나 직무별 판본을 복사해 만듭니다.</span>
                      <Button size="sm" onClick={() => setMaking({ kind: "posting" })}>
                        이 공고용으로 만들기
                      </Button>
                    </div>
                  )}
                </section>
              ) : null}
              <section aria-label="판본">
                {application ? <h2 className="mb-2 text-[15px] font-semibold">모든 판본</h2> : null}
                <ul className="divide-y divide-line border-y border-line">
                  {tree(rows).map(({ row, depth }) => (
                    <VersionRow key={row.id} r={row} depth={depth} />
                  ))}
                </ul>
              </section>
            </div>
          );
        }}
      </Loaded>
      {making ? (
        <NewVersionSheet
          initial={making}
          versions={q.data ?? []}
          applications={apps.data ?? []}
          application={application}
          onClose={() => setMaking(null)}
        />
      ) : null}
    </div>
  );
}

function VersionRow({ r, depth }: { r: ResumeSummary; depth: number }) {
  const parentChanged =
    r.source_revision_no !== null && r.parent_revision !== null && r.parent_revision !== r.source_revision_no;
  const meta = [
    RESUME_KIND_LABEL[r.kind],
    r.target_role,
    r.application_title,
    TEMPLATE_LABEL[r.template],
    r.blind ? "블라인드" : null,
  ].filter(Boolean);
  return (
    <li>
      <Link
        to="/brain/resumes/$versionId"
        params={{ versionId: r.id }}
        className={ROW}
        style={depth ? { paddingLeft: `${0.5 + Math.min(depth, 2) * 1.25}rem` } : undefined}
      >
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-baseline gap-2">
            {depth ? (
              <span aria-hidden className="text-muted">
                └
              </span>
            ) : null}
            <span className="truncate text-sm font-medium">{r.title}</span>
          </div>
          <p className="mt-0.5 truncate text-[13px] text-muted tabular-nums">
            {meta.join(" · ")}
            <span className="max-sm:hidden"> · 수정한 날 {formatDate(r.updated_at)}</span>
          </p>
        </div>
        {parentChanged ? (
          <span className="shrink-0 text-[13px] text-muted max-sm:hidden">위 판본이 바뀜</span>
        ) : null}
        {r.unsupported_lines ? (
          <span className="shrink-0 text-[13px] font-semibold tabular-nums">근거 없는 줄 {r.unsupported_lines}</span>
        ) : null}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function NewVersionSheet({
  initial,
  versions,
  applications,
  application,
  onClose,
}: {
  initial: { kind: ResumeKind; parent?: string };
  versions: ResumeSummary[];
  applications: ApplicationSummary[];
  application?: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [kind, setKind] = useState<ResumeKind>(initial.kind === "master" ? "role" : initial.kind);
  const parents = versions.filter((v) => (kind === "role" ? v.kind === "master" : v.kind !== "posting"));
  const [parent, setParent] = useState(initial.parent ?? parents[0]?.id ?? "");
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [appId, setAppId] = useState(application ?? "");
  useEffect(() => {
    if (!parents.some((p) => p.id === parent)) setParent(parents[0]?.id ?? "");
  }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const create = useMutation({
    mutationFn: () =>
      api.createResume({
        kind,
        parent_id: parent || null,
        title: title.trim() || null,
        target_role: role.trim() || null,
        application_id: kind === "posting" && appId ? appId : null,
      }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["resumes"] });
      onClose();
      void navigate({ to: "/brain/resumes/$versionId", params: { versionId: r.id } });
    },
  });
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title="판본 만들기" description="고른 판본의 지금 내용을 복사해 시작합니다. 복사한 뒤에는 따로 바뀝니다.">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div>
          <Label htmlFor="nv-kind" className="mb-1">
            종류
          </Label>
          <Select id="nv-kind" value={kind} onChange={(e) => setKind(e.target.value as ResumeKind)}>
            <option value="role">직무별 (마스터에서)</option>
            <option value="posting">공고별 (마스터나 직무별에서)</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="nv-parent" className="mb-1">
            시작할 판본
          </Label>
          <Select id="nv-parent" value={parent} onChange={(e) => setParent(e.target.value)}>
            {parents.map((p) => (
              <option key={p.id} value={p.id}>
                {RESUME_KIND_LABEL[p.kind]} · {p.title}
              </option>
            ))}
          </Select>
        </div>
        {kind === "posting" ? (
          <div>
            <Label htmlFor="nv-app" className="mb-1">
              지원
            </Label>
            <Select id="nv-app" value={appId} onChange={(e) => setAppId(e.target.value)}>
              <option value="">잇지 않음</option>
              {applications.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.title}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-[13px] text-muted">
              지원에 이으면 그 지원 준비에서 고른 경험 순서를 쓸 수 있고, 공고에 블라인드 안내가 있으면 알려 줍니다.
            </p>
          </div>
        ) : (
          <div>
            <Label htmlFor="nv-role" className="mb-1">
              직무
            </Label>
            <Input id="nv-role" value={role} onChange={(e) => setRole(e.target.value)} maxLength={200} placeholder="예: 백엔드 개발" />
          </div>
        )}
        <div>
          <Label htmlFor="nv-title" className="mb-1">
            제목
          </Label>
          <Input id="nv-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="비우면 자동으로 붙습니다" />
        </div>
        {create.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {resumeErrorText(create.error) ?? errorText(create.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" disabled={create.isPending || !parent}>
            복사해 만들기
          </Button>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
