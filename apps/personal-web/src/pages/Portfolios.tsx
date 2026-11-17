import type {
  ApplicationSummary,
  PortfolioBundleKind,
  PortfolioBundleSummary,
  PortfolioItemSummary,
} from "@career-brain/client";
import { Button, Checkbox, Input, Label, Select, SelectItem, Sheet } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Plus } from "lucide-react";
import { useState } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { GitHubImportSheet } from "../components/portfolio/GitHubImportSheet";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { BUNDLE_KIND_LABEL, portfolioErrorText, VISIBILITY_LABEL } from "../lib/portfolio";
import { portfoliosRoute } from "../router";

const ROW =
  "-mx-2 flex min-h-9 items-center gap-3 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";

const errText = (e: unknown) => portfolioErrorText(e) ?? errorText(e);

/** 위 묶음 → 아래 묶음 순서 (전체 → 직무별 → 공고별). 위 묶음이 없어진 묶음은 맨 아래. */
function tree(rows: PortfolioBundleSummary[]): { row: PortfolioBundleSummary; depth: number }[] {
  const byParent = new Map<string | null, PortfolioBundleSummary[]>();
  const ids = new Set(rows.map((r) => r.id));
  for (const r of rows) {
    const key = r.parent_id && ids.has(r.parent_id) ? r.parent_id : null;
    byParent.set(key, [...(byParent.get(key) ?? []), r]);
  }
  const out: { row: PortfolioBundleSummary; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const r of byParent.get(parent) ?? []) {
      out.push({ row: r, depth });
      walk(r.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** 브레인 › 나 › 포트폴리오: 경험마다 항목 하나, 항목을 골라 순서를 정한 묶음(전체·직무별·공고별)으로 냅니다. */
export function PortfoliosPage() {
  const { application, experience } = portfoliosRoute.useSearch();
  const items = useQuery({ queryKey: ["portfolio", "items"], queryFn: () => api.portfolioItems() });
  const bundles = useQuery({ queryKey: ["portfolio", "bundles"], queryFn: () => api.portfolioBundles() });
  const apps = useQuery({ queryKey: ["applications"], queryFn: api.applications });
  const [newItem, setNewItem] = useState(!!experience);
  const [newBundle, setNewBundle] = useState<null | PortfolioBundleKind>(null);
  const [importing, setImporting] = useState(false);
  const appTitle = apps.data?.find((a) => a.id === application)?.title;
  const forApp = application ? (bundles.data ?? []).filter((b) => b.application_id === application) : [];

  return (
    <div className="max-w-3xl">
      <PageHeader
        back={{ to: "/brain", label: "브레인" }}
        title="포트폴리오"
        subtitle="결과 줄은 확인한 사실에서 고릅니다."
        actions={
          <>
            <Button size="sm" onClick={() => setImporting(true)}>
              GitHub에서 가져오기
            </Button>
            <Button size="sm" variant="primary" onClick={() => setNewItem(true)}>
              <Plus size={14} /> 항목 만들기
            </Button>
          </>
        }
      />

      {application ? (
        <section aria-label="이 공고용 포트폴리오" className="mb-8">
          <h2 className="text-[15px] font-semibold">이 공고용 포트폴리오</h2>
          <p className="text-[13px] text-muted">
            {appTitle ?? "지원"}에 이은 묶음입니다. 지원 준비에서 고른 경험의 항목부터 넣습니다.
          </p>
          {forApp.length ? (
            <ul className="mt-2 divide-y divide-line border-y border-line">
              {forApp.map((b) => (
                <BundleRow key={b.id} b={b} depth={0} />
              ))}
            </ul>
          ) : (
            <div className="mt-2 flex flex-wrap items-center gap-3 border-t border-line pt-3 text-sm">
              <span className="text-muted">아직 없습니다.</span>
              <Button size="sm" onClick={() => setNewBundle("posting")}>
                이 공고용으로 만들기
              </Button>
            </div>
          )}
        </section>
      ) : null}

      <section aria-label="묶음" className="mb-10">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-[15px] font-semibold">묶음</h2>
          <Button size="sm" variant="ghost" onClick={() => setNewBundle("all")}>
            <Plus size={14} /> 묶음 만들기
          </Button>
        </div>
        <Loaded query={bundles}>
          {(rows) =>
            rows.length ? (
              <ul className="divide-y divide-line border-y border-line">
                {tree(rows).map(({ row, depth }) => (
                  <BundleRow key={row.id} b={row} depth={depth} />
                ))}
              </ul>
            ) : (
              <p className="border-t border-line pt-3 text-[13px] text-muted">
                아직 묶음이 없습니다. 전체 묶음 하나를 만들고, 직무별 묶음은 거기서 복사합니다.
              </p>
            )
          }
        </Loaded>
      </section>

      <section aria-label="항목">
        <h2 className="mb-2 text-[15px] font-semibold">
          항목 <span className="font-normal text-muted tabular-nums">{items.data?.length ?? ""}</span>
        </h2>
        <Loaded query={items}>
          {(rows) =>
            rows.length ? (
              <ul className="divide-y divide-line border-y border-line">
                {rows.map((it) => (
                  <ItemRow key={it.id} it={it} />
                ))}
              </ul>
            ) : (
              <p className="border-t border-line pt-3 text-[13px] text-muted">
                아직 항목이 없습니다. 경험을 고르면 확인한 사실로 한 일·결과 줄을 채웁니다.
              </p>
            )
          }
        </Loaded>
      </section>

      {newItem ? <NewItemSheet initial={experience} onClose={() => setNewItem(false)} /> : null}
      {newBundle ? (
        <NewBundleSheet
          initialKind={newBundle}
          bundles={bundles.data ?? []}
          applications={apps.data ?? []}
          application={application}
          onClose={() => setNewBundle(null)}
        />
      ) : null}
      {importing ? <GitHubImportSheet onClose={() => setImporting(false)} /> : null}
    </div>
  );
}

function ItemRow({ it }: { it: PortfolioItemSummary }) {
  const meta = [
    it.experience_title !== it.title ? it.experience_title : null,
    VISIBILITY_LABEL[it.visibility],
    it.images ? `이미지 ${it.images}` : null,
    it.experience_active ? null : "보관한 경험",
  ].filter(Boolean);
  const marks = [
    it.lines.broken ? `근거 사라짐 ${it.lines.broken}` : null,
    it.lines.stale ? `사실이 바뀜 ${it.lines.stale}` : null,
    !it.results ? "결과 없음" : null,
  ].filter(Boolean);
  return (
    <li>
      <Link to="/brain/portfolio/items/$itemId" params={{ itemId: it.id }} className={ROW}>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{it.title}</p>
          <p className="mt-0.5 truncate text-[13px] text-muted tabular-nums">
            {meta.join(" · ")}
            <span className="max-sm:hidden"> · 수정한 날 {formatDate(it.updated_at)}</span>
          </p>
        </div>
        {marks.length ? (
          <span className="shrink-0 text-[13px] font-semibold tabular-nums">{marks.join(" · ")}</span>
        ) : it.lines.none ? (
          <span className="shrink-0 text-[13px] text-muted tabular-nums">근거 없음 {it.lines.none}</span>
        ) : null}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function BundleRow({ b, depth }: { b: PortfolioBundleSummary; depth: number }) {
  const parentChanged =
    b.source_revision !== null && b.parent_revision !== null && b.parent_revision !== b.source_revision;
  const meta = [BUNDLE_KIND_LABEL[b.kind], b.target_role, b.application_title, `항목 ${b.shown}`].filter(Boolean);
  return (
    <li>
      <Link
        to="/brain/portfolio/$bundleId"
        params={{ bundleId: b.id }}
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
            <span className="truncate text-sm font-medium">{b.title}</span>
          </div>
          <p className="mt-0.5 truncate text-[13px] text-muted tabular-nums">
            {meta.join(" · ")}
            {b.items > b.shown ? ` · 나가지 않는 항목 ${b.items - b.shown}` : ""}
          </p>
        </div>
        {parentChanged ? (
          <span className="shrink-0 text-[13px] text-muted max-sm:hidden">위 묶음이 바뀜</span>
        ) : null}
        <ChevronRight size={16} className="shrink-0 text-muted" />
      </Link>
    </li>
  );
}

function NewItemSheet({ initial, onClose }: { initial?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const exps = useQuery({ queryKey: ["experiences", "portfolio-pick"], queryFn: () => api.experiences({ sort: "start" }) });
  const [exp, setExp] = useState(initial ?? "");
  const [fill, setFill] = useState(true);
  const chosen = exp || exps.data?.[0]?.id || "";
  const create = useMutation({
    mutationFn: () => api.createPortfolioItem({ experience_id: chosen, fill }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["portfolio"] });
      onClose();
      void navigate({ to: "/brain/portfolio/items/$itemId", params: { itemId: r.id } });
    },
  });
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title="항목 만들기" description="항목은 경험 하나에 붙습니다.">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div>
          <Label htmlFor="pi-exp" className="mb-1">
            경험
          </Label>
          <Select id="pi-exp" value={chosen} onValueChange={(v) => setExp(v)} disabled={!exps.data?.length}>
            {(exps.data ?? []).map((x) => (
              <SelectItem key={x.id} value={x.id}>
                {[x.title, x.organization].filter(Boolean).join(" · ")}
              </SelectItem>
            ))}
          </Select>
          {exps.data && !exps.data.length ? (
            <p className="mt-1 text-[13px] text-muted">브레인에 경험이 아직 없습니다. 경험을 먼저 만듭니다.</p>
          ) : null}
        </div>
        <label className="flex min-h-9 items-start gap-2 text-sm">
          <Checkbox className="mt-1" checked={fill} onCheckedChange={(checked) => setFill(checked)} />
          <span>
            확인한 사실로 채우기
            <span className="block text-[13px] text-muted">결과·결정은 결과 줄로, 한 일·맡은 일은 한 일 줄로 5개씩까지 넣습니다.</span>
          </span>
        </label>
        {create.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {errText(create.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" disabled={create.isPending || !chosen}>
            만들기
          </Button>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function NewBundleSheet({
  initialKind,
  bundles,
  applications,
  application,
  onClose,
}: {
  initialKind: PortfolioBundleKind;
  bundles: PortfolioBundleSummary[];
  applications: ApplicationSummary[];
  application?: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [kind, setKind] = useState<PortfolioBundleKind>(
    initialKind === "all" && bundles.some((b) => b.kind === "all") ? "role" : initialKind,
  );
  const parents = bundles.filter((b) => (kind === "role" ? b.kind === "all" : b.kind !== "posting"));
  const [parent, setParent] = useState("");
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [appId, setAppId] = useState(application ?? "");
  const parentId = kind === "all" ? "" : parent === "none" ? "" : parent || parents[0]?.id || "";
  const create = useMutation({
    mutationFn: () =>
      api.createPortfolioBundle({
        kind,
        parent_id: parentId || null,
        title: title.trim() || null,
        target_role: kind === "role" ? role.trim() || null : null,
        application_id: kind === "posting" && appId ? appId : null,
        fill: kind === "all",
      }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["portfolio"] });
      onClose();
      void navigate({ to: "/brain/portfolio/$bundleId", params: { bundleId: r.id } });
    },
  });
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title="묶음 만들기"
      description="직무별·공고별 묶음은 고른 묶음의 항목 목록을 복사해 시작합니다. 복사한 뒤에는 따로 바뀝니다."
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div>
          <Label htmlFor="pb-kind" className="mb-1">
            종류
          </Label>
          <Select id="pb-kind" value={kind} onValueChange={(v) => setKind(v as PortfolioBundleKind)}>
            <SelectItem value="all">전체 (나만이 아닌 항목을 모두 넣고 시작)</SelectItem>
            <SelectItem value="role">직무별 (전체 묶음에서)</SelectItem>
            <SelectItem value="posting">공고별 (전체나 직무별 묶음에서)</SelectItem>
          </Select>
        </div>
        {kind !== "all" ? (
          <div>
            <Label htmlFor="pb-parent" className="mb-1">
              시작할 묶음
            </Label>
            <Select id="pb-parent" value={parentId || "none"} onValueChange={(v) => setParent(v)}>
              {kind === "posting" ? <SelectItem value="none">없음 (지원 준비에서 고른 항목만)</SelectItem> : null}
              {parents.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {BUNDLE_KIND_LABEL[p.kind]} · {p.title}
                </SelectItem>
              ))}
            </Select>
          </div>
        ) : null}
        {kind === "posting" ? (
          <div>
            <Label htmlFor="pb-app" className="mb-1">
              지원
            </Label>
            <Select id="pb-app" value={appId} onValueChange={(v) => setAppId(v)}>
              <SelectItem value="">잇지 않음</SelectItem>
              {applications.map((a) => (
                <SelectItem key={a.id} value={a.id}>
                  {a.title}
                </SelectItem>
              ))}
            </Select>
            <p className="mt-1 text-[13px] text-muted">
              공고별 묶음에는 '지원서에만' 항목과 이미지도 나갑니다. 지원에 이으면 지원 준비에서 고른 경험의 항목을 먼저 놓을 수 있습니다.
            </p>
          </div>
        ) : null}
        {kind === "role" ? (
          <div>
            <Label htmlFor="pb-role" className="mb-1">
              직무
            </Label>
            <Input id="pb-role" value={role} onChange={(e) => setRole(e.target.value)} maxLength={200} placeholder="예: 백엔드 개발" />
          </div>
        ) : null}
        <div>
          <Label htmlFor="pb-title" className="mb-1">
            제목
          </Label>
          <Input id="pb-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="비우면 자동으로 붙습니다" />
        </div>
        {create.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {errText(create.error)}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <Button type="submit" variant="primary" disabled={create.isPending || (kind === "role" && !parentId)}>
            만들기
          </Button>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
