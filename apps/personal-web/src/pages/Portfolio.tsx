import type { PortfolioBundle, PortfolioCheck, PortfolioFormat, Finding } from "@career-brain/client";
import { Button, cn, ConfirmDialog, IconButton, Input, Label, Select, SelectItem, Sheet, Textarea, ViewTabs } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, Download, Plus, Printer, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { FindingList, saveBlob } from "../components/doc/ExportSheet";
import { ResumePreview } from "../components/resume/ResumePreview";
import { api } from "../lib/api";
import { BUNDLE_KIND_LABEL, isConflict, portfolioErrorText, VISIBILITY_LABEL } from "../lib/portfolio";
import { RESUME_KIND_LABEL } from "../lib/resume";
import { portfolioBundleRoute } from "../router";

/** 넓은 화면(편집 + 미리보기 두 칸). Tailwind lg 와 같다. */
const WIDE = "(min-width: 1024px)";

function useMedia(query: string): boolean {
  const [match, setMatch] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

const errText = (e: unknown) => portfolioErrorText(e) ?? errorText(e);

const AUDIENCE_NOTE = {
  public: "'공개 가능' 항목·이미지만 나갑니다.",
  application: "'공개 가능'과 '지원서에만' 항목·이미지가 나갑니다.",
} as const;

/** 묶음 하나: 위에 체크리스트, 왼쪽 항목 순서, 오른쪽 A4 미리보기 (좁은 화면은 편집/미리보기 탭). */
export function PortfolioPage() {
  const { bundleId } = portfolioBundleRoute.useParams();
  const q = useQuery({ queryKey: ["portfolio", "bundle", bundleId], queryFn: () => api.portfolioBundle(bundleId) });
  return <Loaded query={q}>{(b) => <Editor key={b.summary.id} b={b} />}</Loaded>;
}

function Editor({ b }: { b: PortfolioBundle }) {
  const id = b.summary.id;
  const qc = useQueryClient();
  const wide = useMedia(WIDE);
  const [tab, setTab] = useState<"edit" | "preview">("edit");
  const [printNonce, setPrintNonce] = useState(0);
  const [pages, setPages] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [exporting, setExporting] = useState(false);
  const stamp = `${b.summary.revision}-${b.summary.updated_at}-${b.items.map((x) => x.item.updated_at).join()}`;
  const check = useQuery({ queryKey: ["portfolio", "bundle", id, "check", stamp], queryFn: () => api.portfolioCheck(id) });
  const preview = useQuery({
    queryKey: ["portfolio", "bundle", id, "preview", stamp],
    queryFn: () => api.portfolioPreview(id),
    placeholderData: (prev) => prev,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["portfolio"] });
  const ids = b.items.map((x) => x.item.id);
  const order = useMutation({
    mutationFn: (next: string[]) => api.setPortfolioBundleItems(id, { base_revision: b.summary.revision, item_ids: next }),
    onSuccess: () => {
      setNotice(null);
      setAdding(false);
      refresh();
    },
    onError: (e) => {
      setNotice(errText(e));
      if (isConflict(e)) refresh();
    },
  });
  const prep = useMutation({
    mutationFn: () => api.applyPortfolioPrepOrder(id, b.summary.revision),
    onSuccess: refresh,
    onError: (e) => setNotice(errText(e)),
  });
  const settings = useMutation({
    mutationFn: (body: Parameters<typeof api.patchPortfolioBundle>[1]) => api.patchPortfolioBundle(id, body),
    onSuccess: refresh,
    onError: (e) => setNotice(errText(e)),
  });
  const print = () => {
    if (!wide) setTab("preview");
    setPrintNonce((n) => n + 1);
  };
  const onPages = useCallback((n: number) => setPages(n), []);
  const move = (from: number, to: number) => {
    const next = [...ids];
    const [x] = next.splice(from, 1);
    next.splice(to, 0, x!);
    order.mutate(next);
  };
  const busy = order.isPending || prep.isPending;
  const lineage = b.lineage.map((x) => x.title).join(" › ");
  const prepFirst = b.prep_order.length > 0 && b.prep_order.some((x, i) => ids[i] !== x);

  return (
    <div>
      <PageHeader
        back={{ to: "/brain/portfolio", label: "포트폴리오" }}
        title={b.summary.title}
        subtitle={
          <span className="tabular-nums">
            {BUNDLE_KIND_LABEL[b.summary.kind]}
            {lineage ? ` · ${lineage}에서 복사` : ""}
            {b.summary.application_id ? (
              <>
                {" · "}
                <Link
                  to="/applications/$applicationId"
                  params={{ applicationId: b.summary.application_id }}
                  className="underline underline-offset-2 hover:text-fg"
                >
                  {b.summary.application_title ?? "지원"}
                </Link>
              </>
            ) : null}
            {" · "}
            {AUDIENCE_NOTE[b.summary.audience]}
          </span>
        }
        actions={
          <>
            <Button size="sm" onClick={print} disabled={!b.summary.shown}>
              <Printer size={14} /> 인쇄·PDF
            </Button>
            <Button size="sm" onClick={() => setExporting(true)} disabled={!b.summary.shown}>
              <Download size={14} /> 내보내기
            </Button>
            <DeleteBundle b={b} />
          </>
        }
      />
      <Checklist check={check.data} />
      {prepFirst ? (
        <div className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-2 border-y border-line py-2.5 text-[13px]">
          <span className="min-w-0 flex-1 text-muted">
            지원 준비에서 고른 경험의 항목 {b.prep_order.length}개를 맨 앞에 놓을 수 있습니다.
          </span>
          <Button size="sm" disabled={busy} onClick={() => prep.mutate()}>
            고른 순서로 놓기
          </Button>
        </div>
      ) : null}
      <SettingsRow b={b} busy={settings.isPending} onChange={(x) => settings.mutate(x)} />
      {notice ? (
        <p role="alert" className="mb-3 text-[13px] text-danger">
          {notice}
        </p>
      ) : null}
      {!wide ? (
        <ViewTabs
          label="보기"
          value={tab}
          onChange={setTab}
          items={[
            { value: "edit", label: "편집" },
            { value: "preview", label: "미리보기" },
          ]}
          className="mb-4"
        />
      ) : null}
      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-8">
        {wide || tab === "edit" ? (
          <section aria-label="항목 순서" className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 border-b border-line pb-1.5">
              <h2 className="min-w-0 flex-1 text-[15px] font-semibold">
                항목 <span className="font-normal text-muted tabular-nums">{b.items.length}</span>
              </h2>
              <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
                <Plus size={14} /> 더하기
              </Button>
            </div>
            {b.items.length ? (
              <ol className="divide-y divide-line">
                {b.items.map((x, i) => (
                  <li key={x.item.id} className={cn("flex items-start gap-2 py-3", !x.shown && "opacity-60")}>
                    <span className="w-5 shrink-0 pt-0.5 text-right text-[13px] text-muted tabular-nums">{i + 1}</span>
                    <div className="min-w-0 flex-1">
                      <Link
                        to="/brain/portfolio/items/$itemId"
                        params={{ itemId: x.item.id }}
                        className="inline-flex min-h-9 items-center text-sm font-medium break-words underline-offset-2 hover:underline sm:min-h-6"
                      >
                        {x.item.title}
                      </Link>
                      <p className="text-[13px] text-muted tabular-nums">
                        {[
                          x.item.experience_title !== x.item.title ? x.item.experience_title : null,
                          VISIBILITY_LABEL[x.item.visibility],
                          x.images_shown ? `이미지 ${x.images_shown}` : null,
                          x.images_hidden ? `나가지 않는 이미지 ${x.images_hidden}` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      {!x.shown ? (
                        <p className="text-[13px] font-medium">
                          {x.hidden_reason === "private"
                            ? "'나만' 항목이라 나가지 않습니다."
                            : "'지원서에만' 항목이라 공고별 묶음에서만 나갑니다."}
                        </p>
                      ) : null}
                    </div>
                    <IconButton label="위로" disabled={busy || i === 0} onClick={() => move(i, i - 1)}>
                      <ArrowUp size={15} />
                    </IconButton>
                    <IconButton
                     
                     
                      label="아래로"
                     
                      disabled={busy || i === b.items.length - 1}
                      onClick={() => move(i, i + 1)}
                    >
                      <ArrowDown size={15} />
                    </IconButton>
                    <IconButton
                     
                     
                      label="묶음에서 빼기"
                     
                      disabled={busy}
                      onClick={() => order.mutate(ids.filter((y) => y !== x.item.id))}
                    >
                      <Trash2 size={15} />
                    </IconButton>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="py-3 text-[13px] text-muted">비어 있습니다. 항목을 더하면 미리보기에 바로 보입니다.</p>
            )}
          </section>
        ) : null}
        {wide || tab === "preview" ? (
          <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
            <p className="mb-2 text-[13px] text-muted tabular-nums">
              A4 {pages ?? "–"}쪽 · 나가는 항목 {b.summary.shown}개
            </p>
            <ResumePreview html={preview.data} printNonce={printNonce} onPages={onPages} title="포트폴리오 미리보기" />
          </div>
        ) : null}
      </div>
      {adding ? (
        <AddItemsSheet b={b} busy={busy} onClose={() => setAdding(false)} onAdd={(itemId) => order.mutate([...ids, itemId])} />
      ) : null}
      {exporting ? <ExportSheet b={b} onClose={() => setExporting(false)} onPrint={print} /> : null}
    </div>
  );
}

function Checklist({ check }: { check: PortfolioCheck | undefined }) {
  if (!check) return null;
  const items = check.checklist.filter((c) => c.code !== "prep_order");
  return (
    <section aria-label="체크리스트" className="mb-6">
      <h2 className="text-[15px] font-semibold">체크리스트</h2>
      {items.length ? (
        <ul className="mt-2 divide-y divide-line border-y border-line">
          {items.map((c) => (
            <li key={c.code} className={cn("py-2.5 text-[13px]", c.severity !== "info" && "font-medium")}>
              {c.message}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 border-t border-line pt-2 text-[13px] text-muted">지금은 볼 것이 없습니다.</p>
      )}
    </section>
  );
}

function SettingsRow({
  b,
  busy,
  onChange,
}: {
  b: PortfolioBundle;
  busy: boolean;
  onChange: (x: { title?: string; target_role?: string | null; intro?: string | null }) => void;
}) {
  const s = b.summary;
  const [title, setTitle] = useState(s.title);
  const [role, setRole] = useState(s.target_role ?? "");
  const [intro, setIntro] = useState(s.intro ?? "");
  return (
    <div className="mb-6 grid gap-3 border-b border-line pb-4 sm:grid-cols-2">
      <div>
        <Label htmlFor="pb-title" className="mb-1">
          제목
        </Label>
        <Input
          id="pb-title"
          value={title}
          maxLength={200}
          disabled={busy}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title.trim() && title.trim() !== s.title && onChange({ title: title.trim() })}
        />
      </div>
      <div>
        <Label htmlFor="pb-role" className="mb-1">
          직무
        </Label>
        <Input
          id="pb-role"
          value={role}
          maxLength={200}
          disabled={busy}
          placeholder="표지에 함께 나갑니다"
          onChange={(e) => setRole(e.target.value)}
          onBlur={() => role.trim() !== (s.target_role ?? "") && onChange({ target_role: role.trim() || null })}
        />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor="pb-intro" className="mb-1">
          소개
        </Label>
        <Textarea
          id="pb-intro"
          rows={2}
          value={intro}
          maxLength={1000}
          disabled={busy}
          placeholder="표지에 한두 문장 (비워도 됩니다)"
          onChange={(e) => setIntro(e.target.value)}
          onBlur={() => intro.trim() !== (s.intro ?? "") && onChange({ intro: intro.trim() || null })}
        />
      </div>
    </div>
  );
}

function AddItemsSheet({
  b,
  busy,
  onClose,
  onAdd,
}: {
  b: PortfolioBundle;
  busy: boolean;
  onClose: () => void;
  onAdd: (itemId: string) => void;
}) {
  const items = useQuery({ queryKey: ["portfolio", "items"], queryFn: () => api.portfolioItems() });
  const inBundle = new Set(b.items.map((x) => x.item.id));
  const prep = new Set(b.prep_order);
  const rest = (items.data ?? []).filter((x) => !inBundle.has(x.id));
  const blocked = (v: string) => v === "private" || (v === "application" && b.summary.audience === "public");
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title="항목 더하기" description={AUDIENCE_NOTE[b.summary.audience]}>
      <Loaded query={items}>
        {() =>
          rest.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {rest.map((it) => (
                <li key={it.id} className="flex items-start gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium break-words">{it.title}</p>
                    <p className="text-[13px] text-muted">
                      {[it.experience_title !== it.title ? it.experience_title : null, VISIBILITY_LABEL[it.visibility], prep.has(it.id) ? "지원 준비에서 고른 경험" : null]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                    {blocked(it.visibility) ? (
                      <p className="text-[13px] text-muted">넣어도 이 묶음에서는 나가지 않습니다.</p>
                    ) : null}
                  </div>
                  <Button size="sm" disabled={busy} onClick={() => onAdd(it.id)}>
                    넣기
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-muted">더 넣을 항목이 없습니다. 경험 화면에서 포트폴리오 항목을 만듭니다.</p>
          )
        }
      </Loaded>
    </Sheet>
  );
}

const FORMATS: { fmt: PortfolioFormat; title: string; note: string }[] = [
  {
    fmt: "html",
    title: "인쇄용 HTML",
    note: "미리보기와 같은 파일 하나입니다. 이미지도 파일 안에 들어 있습니다.",
  },
  {
    fmt: "zip",
    title: "정적 HTML 묶음 (zip)",
    note: "index.html과 이미지 폴더입니다. 풀어서 서버 없이 열거나 그대로 올립니다.",
  },
];

function ExportSheet({ b, onClose, onPrint }: { b: PortfolioBundle; onClose: () => void; onPrint: () => void }) {
  const resumes = useQuery({ queryKey: ["resumes"], queryFn: () => api.resumes() });
  const [resume, setResume] = useState("");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const get = useMutation({
    mutationFn: async (fmt: PortfolioFormat) => {
      const withResume = fmt === "zip" && resume ? resume : undefined;
      const check = await api.portfolioExportCheck(b.summary.id, fmt, withResume);
      const file = await api.portfolioExportFile(b.summary.id, fmt, withResume);
      return { check, file };
    },
    onSuccess: ({ check, file }) => {
      setFindings(check.findings as Finding[]);
      saveBlob(file.blob, file.filename);
    },
  });
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title="내보내기" description={AUDIENCE_NOTE[b.summary.audience]}>
      <ul className="divide-y divide-line border-y border-line">
        <li className="flex items-start gap-3 py-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">인쇄·PDF로 저장</p>
            <p className="text-[13px] text-muted">미리보기를 그대로 인쇄합니다. 인쇄 창에서 'PDF로 저장'을 고릅니다.</p>
          </div>
          <Button
            size="sm"
            onClick={() => {
              onClose();
              onPrint();
            }}
          >
            <Printer size={14} /> 인쇄
          </Button>
        </li>
        {FORMATS.map(({ fmt, title, note }) => (
          <li key={fmt} className="py-3">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{title}</p>
                <p className="text-[13px] text-muted">{note}</p>
              </div>
              <Button size="sm" disabled={get.isPending} onClick={() => get.mutate(fmt)}>
                <Download size={14} /> 받기
              </Button>
            </div>
            {fmt === "zip" ? (
              <div className="mt-2">
                <Label htmlFor="pf-resume" className="mb-1">
                  함께 넣을 이력서
                </Label>
                <Select id="pf-resume" value={resume} onValueChange={(v) => setResume(v)}>
                  <SelectItem value="">넣지 않음</SelectItem>
                  {(resumes.data ?? []).map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {RESUME_KIND_LABEL[r.kind]} · {r.title}
                    </SelectItem>
                  ))}
                </Select>
                <p className="mt-1 text-[13px] text-muted">
                  넣으면 이력서 프로젝트 항목에서 같은 경험의 포트폴리오 항목으로 링크가 이어집니다.
                </p>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {get.error ? <p className="mt-3 text-[13px] text-danger">{errText(get.error)}</p> : null}
      {findings?.length ? (
        <div className="mt-4">
          <p className="mb-1 text-[13px] font-medium">받은 파일에서 본 것</p>
          <FindingList findings={findings} />
        </div>
      ) : null}
    </Sheet>
  );
}

function DeleteBundle({ b }: { b: PortfolioBundle }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const del = useMutation({
    mutationFn: () => api.deletePortfolioBundle(b.summary.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["portfolio"] });
      void navigate({ to: "/brain/portfolio" });
    },
  });
  return (
    <>
      <Button size="sm" variant="ghost" className="text-danger" onClick={() => setOpen(true)}>
        <Trash2 size={14} /> 삭제
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="묶음을 삭제할까요?"
        description={
          b.summary.children
            ? "이 묶음에서 만든 묶음이 있어 삭제할 수 없습니다. 아래 묶음부터 삭제합니다."
            : `'${b.summary.title}' 묶음만 사라집니다. 항목은 그대로 남습니다.`
        }
        confirmLabel="삭제"
        tone="danger"
        busy={del.isPending}
        confirmDisabled={b.summary.children > 0}
        onConfirm={() => del.mutate()}
      >
        {del.error ? <p className="text-[13px] text-danger">{errText(del.error)}</p> : null}
      </ConfirmDialog>
    </>
  );
}
