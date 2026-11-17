import type {
  Finding,
  ResumeCheck,
  ResumeFormat,
  ResumeItem,
  ResumeLine,
  ResumeLineSuggestion,
  ResumeOperation,
  ResumeSection,
  ResumeTemplate,
  ResumeVersion,
} from "@career-brain/client";
import { RESUME_TEMPLATES } from "@career-brain/client";
import { Button, cn, ConfirmDialog, Label, Select, Sheet, Textarea, ViewTabs } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowDown,
  ArrowUp,
  Download,
  Eye,
  EyeOff,
  MoreHorizontal,
  Plus,
  Printer,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { FindingList, saveBlob } from "../components/doc/ExportSheet";
import { ItemPicker, LinePicker } from "../components/resume/BrainPicker";
import { ResumePreview } from "../components/resume/ResumePreview";
import { api } from "../lib/api";
import {
  FORMAT_LABEL,
  isConflict,
  itemTitle,
  LINE_STATE_LABEL,
  PROFILE_SHOW_LABEL,
  RESUME_KIND_LABEL,
  resumeErrorText,
  SECTION_LABEL,
  TEMPLATE_LABEL,
} from "../lib/resume";
import { resumeRoute } from "../router";

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

const errText = (e: unknown) => resumeErrorText(e) ?? errorText(e);

/** 판본 하나: 위에 체크리스트, 왼쪽 구역 편집, 오른쪽 A4 미리보기 (좁은 화면은 편집/미리보기 탭). */
export function ResumePage() {
  const { versionId } = resumeRoute.useParams();
  const q = useQuery({ queryKey: ["resume", versionId], queryFn: () => api.resume(versionId) });
  return (
    <Loaded query={q}>
      {(v) => <Editor key={v.summary.id} v={v} />}
    </Loaded>
  );
}

function Editor({ v }: { v: ResumeVersion }) {
  const id = v.summary.id;
  const qc = useQueryClient();
  const wide = useMedia(WIDE);
  const [tab, setTab] = useState<"edit" | "preview">("edit");
  const [printNonce, setPrintNonce] = useState(0);
  const [pages, setPages] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [picker, setPicker] = useState<ResumeSection["kind"] | null>(null);
  const [linePicker, setLinePicker] = useState<ResumeItem | null>(null);
  const [exporting, setExporting] = useState(false);
  const stamp = `${v.revision_no}-${v.summary.template}-${v.summary.blind}-${v.summary.updated_at}`;
  const check = useQuery({ queryKey: ["resume", id, "check", stamp], queryFn: () => api.resumeCheck(id) });
  const preview = useQuery({
    queryKey: ["resume", id, "preview", stamp],
    queryFn: () => api.resumePreview(id),
    placeholderData: (prev) => prev,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["resume", id] });
    void qc.invalidateQueries({ queryKey: ["resumes"] });
  };
  const save = useMutation({
    mutationFn: (ops: ResumeOperation[]) =>
      api.saveResume(id, { base_revision: v.revision_no, operations: ops }),
    onSuccess: () => {
      setNotice(null);
      setPicker(null);
      setLinePicker(null);
      refresh();
    },
    onError: (e) => {
      setNotice(errText(e));
      if (isConflict(e)) refresh();
    },
  });
  const settings = useMutation({
    mutationFn: (body: Parameters<typeof api.patchResume>[1]) => api.patchResume(id, body),
    onSuccess: refresh,
    onError: (e) => setNotice(errText(e)),
  });
  const run = useCallback((ops: ResumeOperation[]) => save.mutate(ops), [save]);
  const print = () => {
    if (!wide) setTab("preview");
    setPrintNonce((n) => n + 1);
  };
  const onPages = useCallback((n: number) => setPages(n), []);
  const lineage = v.lineage.map((x) => x.title).join(" › ");

  return (
    <div>
      <PageHeader
        back={{ to: "/brain/resumes", label: "이력서" }}
        title={v.summary.title}
        subtitle={
          <span className="tabular-nums">
            {RESUME_KIND_LABEL[v.summary.kind]}
            {lineage ? ` · ${lineage}에서 복사` : ""}
            {v.summary.application_id ? (
              <>
                {" · "}
                <Link
                  to="/applications/$applicationId"
                  params={{ applicationId: v.summary.application_id }}
                  className="underline underline-offset-2 hover:text-fg"
                >
                  {v.summary.application_title ?? "지원"}
                </Link>
              </>
            ) : null}
          </span>
        }
        actions={
          <>
            <Button size="sm" onClick={print}>
              <Printer size={14} /> 인쇄·PDF
            </Button>
            <Button size="sm" onClick={() => setExporting(true)}>
              <Download size={14} /> 내보내기
            </Button>
            <MoreMenu v={v} />
          </>
        }
      />
      <Checklist v={v} check={check.data} pages={pages} onRun={run} onBlind={() => settings.mutate({ blind: true })} busy={save.isPending} />
      <SettingsRow v={v} onChange={(b) => settings.mutate(b)} busy={settings.isPending} />
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
          <div className="min-w-0">
            {v.content.sections.map((sec, i) => (
              <SectionBlock
                key={sec.kind}
                sec={sec}
                index={i}
                total={v.content.sections.length}
                versionId={id}
                check={check.data}
                revision={v.revision_no}
                onRun={run}
                busy={save.isPending}
                onAddItem={() => setPicker(sec.kind)}
                onAddLine={setLinePicker}
              />
            ))}
          </div>
        ) : null}
        {wide || tab === "preview" ? (
          <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
            <p className="mb-2 text-[13px] text-muted tabular-nums">
              A4 {pages ?? check.data?.estimated_pages ?? "–"}쪽 · 한도 {v.summary.max_pages}쪽
              {v.summary.blind ? " · 블라인드" : ""}
            </p>
            <ResumePreview html={preview.data} printNonce={printNonce} onPages={onPages} />
          </div>
        ) : null}
      </div>
      {picker ? (
        <ItemPicker
          versionId={id}
          section={picker}
          onClose={() => setPicker(null)}
          onAdd={run}
          busy={save.isPending}
          error={save.error ? errText(save.error) : null}
        />
      ) : null}
      {linePicker ? (
        <LinePicker
          versionId={id}
          item={linePicker}
          onClose={() => setLinePicker(null)}
          onAdd={run}
          busy={save.isPending}
          error={save.error ? errText(save.error) : null}
        />
      ) : null}
      {exporting ? <ExportResumeSheet versionId={id} onClose={() => setExporting(false)} onPrint={print} /> : null}
    </div>
  );
}

// ---------------------------------------------------------------- 체크리스트·설정

function Checklist({
  v,
  check,
  pages,
  onRun,
  onBlind,
  busy,
}: {
  v: ResumeVersion;
  check: ResumeCheck | undefined;
  pages: number | null;
  onRun: (ops: ResumeOperation[]) => void;
  onBlind: () => void;
  busy: boolean;
}) {
  if (!check) return null;
  // 화면에서 실제로 잰 쪽 수가 있으면 그것으로 넘침을 본다 (서버는 어림만 한다)
  const items = check.checklist.filter((c) => c.code !== "page_overflow" || pages === null);
  const overflow = pages !== null && pages > v.summary.max_pages;
  const empty = !items.length && !overflow;
  return (
    <section aria-label="체크리스트" className="mb-6">
      <h2 className="text-[15px] font-semibold">체크리스트</h2>
      {empty ? (
        <p className="mt-1 border-t border-line pt-2 text-[13px] text-muted">지금은 볼 것이 없습니다.</p>
      ) : (
        <ul className="mt-2 divide-y divide-line border-y border-line">
          {overflow ? (
            <li className="py-2.5 text-[13px]">
              쪽 수가 한도 {v.summary.max_pages}쪽을 넘습니다 (미리보기 {pages}쪽). 줄을 줄이거나 구역을 숨깁니다.
            </li>
          ) : null}
          {items.map((c) => (
            <li key={c.code} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-[13px]">
              <span className={cn("min-w-0 flex-1", c.severity !== "info" && "font-medium")}>{c.message}</span>
              {c.code === "blind_suggested" ? (
                <Button size="sm" onClick={onBlind}>
                  블라인드 켜기
                </Button>
              ) : null}
              {c.code === "empty_required_section" && v.content.sections.every((s) => !s.items.length) ? (
                <Button size="sm" disabled={busy} onClick={() => onRun([{ op: "fill_all", claims: true }])}>
                  브레인에서 채우기
                </Button>
              ) : null}
            </li>
          ))}
          {v.summary.kind === "posting" && v.prep_choices ? (
            <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-[13px]">
              <span className="min-w-0 flex-1 text-muted">지원 준비에서 고른 경험 {v.prep_choices}개가 있습니다.</span>
              <Button size="sm" disabled={busy} onClick={() => onRun([{ op: "apply_prep_order" }])}>
                고른 순서로 놓기
              </Button>
            </li>
          ) : null}
        </ul>
      )}
    </section>
  );
}

function SettingsRow({
  v,
  onChange,
  busy,
}: {
  v: ResumeVersion;
  onChange: (b: { template?: ResumeTemplate; blind?: boolean; max_pages?: number }) => void;
  busy: boolean;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end gap-x-5 gap-y-3 border-b border-line pb-4">
      <div>
        <Label htmlFor="rs-template" className="mb-1">
          양식
        </Label>
        <Select
          id="rs-template"
          value={v.summary.template}
          disabled={busy}
          onChange={(e) => onChange({ template: e.target.value as ResumeTemplate })}
        >
          {RESUME_TEMPLATES.map((t) => (
            <option key={t} value={t}>
              {TEMPLATE_LABEL[t]}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="rs-pages" className="mb-1">
          쪽 수 한도
        </Label>
        <Select
          id="rs-pages"
          value={String(v.summary.max_pages)}
          disabled={busy}
          onChange={(e) => onChange({ max_pages: Number(e.target.value) })}
        >
          {[1, 2, 3, 4, 5].map((n) => (
            <option key={n} value={n}>
              {n}쪽
            </option>
          ))}
        </Select>
      </div>
      <label className="flex min-h-9 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={v.summary.blind}
          disabled={busy}
          onChange={(e) => onChange({ blind: e.target.checked })}
        />
        블라인드
        <span className="text-[13px] text-muted max-sm:hidden">사진·생년월일·주소·학교 이름을 빼고 냅니다</span>
      </label>
    </div>
  );
}

function MoreMenu({ v }: { v: ResumeVersion }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: () => api.deleteResume(v.summary.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["resumes"] });
      void navigate({ to: "/brain/resumes" });
    },
  });
  return (
    <>
      <Button size="sm" variant="ghost" aria-label="더 보기" onClick={() => setOpen(true)}>
        <MoreHorizontal size={16} />
      </Button>
      <Sheet open={open} onOpenChange={setOpen} title="판본">
        <ul className="divide-y divide-line border-y border-line text-sm">
          <li className="py-3">
            <Link to="/brain/resumes" className="underline underline-offset-2">
              이 판본에서 직무별·공고별 판본 만들기
            </Link>
            <p className="text-[13px] text-muted">이력서 목록의 '판본 만들기'에서 이 판본을 고릅니다.</p>
          </li>
          <li className="py-3">
            <button type="button" className="min-h-9 text-danger" onClick={() => setConfirm(true)}>
              판본 삭제
            </button>
            <p className="text-[13px] text-muted">
              {v.summary.children ? "이 판본에서 만든 판본이 있어 삭제할 수 없습니다." : "저장한 내용이 모두 사라집니다."}
            </p>
          </li>
        </ul>
      </Sheet>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="판본을 삭제할까요?"
        description={`'${v.summary.title}'의 저장한 내용이 모두 사라집니다. 되돌릴 수 없습니다.`}
        confirmLabel="삭제"
        tone="danger"
        busy={del.isPending}
        confirmDisabled={v.summary.children > 0}
        onConfirm={() => del.mutate()}
      >
        {del.error ? <p className="text-[13px] text-danger">{errText(del.error)}</p> : null}
      </ConfirmDialog>
    </>
  );
}

// ---------------------------------------------------------------- 구역·항목·줄

function IconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Button size="icon" variant="ghost" aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      {children}
    </Button>
  );
}

function SectionBlock({
  sec,
  index,
  total,
  versionId,
  check,
  revision,
  onRun,
  busy,
  onAddItem,
  onAddLine,
}: {
  sec: ResumeSection;
  index: number;
  total: number;
  versionId: string;
  check: ResumeCheck | undefined;
  revision: number;
  onRun: (ops: ResumeOperation[]) => void;
  busy: boolean;
  onAddItem: () => void;
  onAddLine: (it: ResumeItem) => void;
}) {
  const title = sec.title ?? SECTION_LABEL[sec.kind];
  const canAdd = sec.kind !== "profile" || !sec.items.length;
  return (
    <section aria-label={title} className={cn("mb-6", !sec.visible && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1 border-b border-line pb-1.5">
        <h2 className="min-w-0 flex-1 text-[15px] font-semibold">
          {title}
          <span className="ml-2 text-[13px] font-normal text-muted tabular-nums">{sec.items.length}</span>
          {!sec.visible ? <span className="ml-2 text-[13px] font-normal text-muted">숨김</span> : null}
        </h2>
        <IconButton label={`${title} 구역 위로`} disabled={busy || index === 0} onClick={() => onRun([{ op: "move_section", section: sec.kind, index: index - 1 }])}>
          <ArrowUp size={15} />
        </IconButton>
        <IconButton label={`${title} 구역 아래로`} disabled={busy || index === total - 1} onClick={() => onRun([{ op: "move_section", section: sec.kind, index: index + 1 }])}>
          <ArrowDown size={15} />
        </IconButton>
        <IconButton
          label={sec.visible ? `${title} 구역 숨기기` : `${title} 구역 보이기`}
          disabled={busy}
          onClick={() => onRun([{ op: "set_section", section: sec.kind, visible: !sec.visible }])}
        >
          {sec.visible ? <EyeOff size={15} /> : <Eye size={15} />}
        </IconButton>
        {canAdd ? (
          <Button size="sm" variant="ghost" onClick={onAddItem}>
            <Plus size={14} /> 더하기
          </Button>
        ) : null}
      </div>
      {sec.items.length ? (
        <ul className="divide-y divide-line">
          {sec.items.map((it, i) => (
            <ItemRow
              key={it.id}
              sec={sec}
              it={it}
              index={i}
              versionId={versionId}
              check={check}
              revision={revision}
              onRun={onRun}
              busy={busy}
              onAddLine={() => onAddLine(it)}
            />
          ))}
        </ul>
      ) : (
        <p className="py-3 text-[13px] text-muted">비어 있습니다.</p>
      )}
    </section>
  );
}

function ItemRow({
  sec,
  it,
  index,
  versionId,
  check,
  revision,
  onRun,
  busy,
  onAddLine,
}: {
  sec: ResumeSection;
  it: ResumeItem;
  index: number;
  versionId: string;
  check: ResumeCheck | undefined;
  revision: number;
  onRun: (ops: ResumeOperation[]) => void;
  busy: boolean;
  onAddLine: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { title, sub } = itemTitle(sec.kind, it);
  const withLines = sec.kind !== "profile" && sec.kind !== "skills";
  return (
    <li className={cn("py-3", it.hidden && "opacity-60")}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium break-words">{title}</p>
          {sub ? <p className="text-[13px] break-words text-muted tabular-nums">{sub}</p> : null}
          <p className="text-[13px] text-muted">
            {[
              it.state === "manual" ? "직접 쓴 항목" : null,
              it.state === "stale" ? "브레인 값이 바뀜" : null,
              it.state === "missing" ? "브레인에 없음" : null,
              it.hidden ? "숨김" : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {it.state === "stale" ? (
          <Button size="sm" disabled={busy} onClick={() => onRun([{ op: "refill_item", item_id: it.id }])}>
            <RefreshCw size={14} /> 다시 채우기
          </Button>
        ) : null}
        <IconButton label={open ? "항목 메뉴 닫기" : "항목 메뉴"} onClick={() => setOpen((o) => !o)}>
          <MoreHorizontal size={16} />
        </IconButton>
      </div>
      {open ? (
        <div className="mt-1 flex flex-wrap gap-1" role="group" aria-label={`${title} 항목`}>
          <Button size="sm" variant="ghost" disabled={busy || index === 0} onClick={() => onRun([{ op: "move_item", item_id: it.id, index: index - 1 }])}>
            <ArrowUp size={14} /> 위로
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || index === sec.items.length - 1}
            onClick={() => onRun([{ op: "move_item", item_id: it.id, index: index + 1 }])}
          >
            <ArrowDown size={14} /> 아래로
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRun([{ op: "update_item", item_id: it.id, hidden: !it.hidden }])}>
            {it.hidden ? <Eye size={14} /> : <EyeOff size={14} />} {it.hidden ? "보이기" : "숨기기"}
          </Button>
          <Button size="sm" variant="ghost" className="text-danger" disabled={busy} onClick={() => onRun([{ op: "remove_item", item_id: it.id }])}>
            <Trash2 size={14} /> 빼기
          </Button>
        </div>
      ) : null}
      {sec.kind === "profile" ? <ProfileShow it={it} busy={busy} onRun={onRun} /> : null}
      {withLines ? (
        <>
          {it.lines.length ? (
            <ul className="mt-2 space-y-1">
              {it.lines.map((ln, i) => (
                <LineRow
                  key={ln.id}
                  ln={ln}
                  index={i}
                  count={it.lines.length}
                  versionId={versionId}
                  revision={revision}
                  flagged={check?.findings.some((f) => f.block_id === ln.id) ?? false}
                  onRun={onRun}
                  busy={busy}
                />
              ))}
            </ul>
          ) : null}
          <Button size="sm" variant="ghost" className="mt-1 -ml-2.5 text-muted" onClick={onAddLine}>
            <Plus size={14} /> 줄 더하기
          </Button>
        </>
      ) : null}
    </li>
  );
}

function ProfileShow({ it, busy, onRun }: { it: ResumeItem; busy: boolean; onRun: (ops: ResumeOperation[]) => void }) {
  const show = it.show ?? {};
  return (
    <fieldset className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
      <legend className="sr-only">프로필에서 보일 칸</legend>
      {Object.entries(PROFILE_SHOW_LABEL).map(([k, label]) => (
        <label key={k} className="flex min-h-9 items-center gap-1.5 text-[13px] sm:min-h-7">
          <input
            type="checkbox"
            checked={!!show[k]}
            disabled={busy}
            onChange={(e) => onRun([{ op: "update_item", item_id: it.id, show: { ...show, [k]: e.target.checked } }])}
          />
          {label}
        </label>
      ))}
    </fieldset>
  );
}

function LineRow({
  ln,
  index,
  count,
  versionId,
  revision,
  flagged,
  onRun,
  busy,
}: {
  ln: ResumeLine;
  index: number;
  count: number;
  versionId: string;
  revision: number;
  flagged: boolean;
  onRun: (ops: ResumeOperation[]) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(ln.text);
  const [suggestion, setSuggestion] = useState<ResumeLineSuggestion | null>(null);
  const suggest = useMutation({
    mutationFn: () => api.suggestResumeLines(versionId, { base_revision: revision, line_ids: [ln.id] }),
    onSuccess: (r) => setSuggestion(r.suggestions[0] ?? null),
  });
  const mark = LINE_STATE_LABEL[ln.state];
  return (
    <li className="text-sm leading-6">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="-mx-2 flex w-[calc(100%+1rem)] items-start gap-2 rounded-md px-2 py-1 text-left transition-colors duration-150 hover:bg-subtle/60"
      >
        <span aria-hidden className="text-muted">
          ·
        </span>
        <span className="min-w-0 flex-1 break-words">
          {ln.text}
          {mark ? (
            <span className={cn("ml-2 text-[13px] text-muted", ln.state === "broken" && "font-semibold text-fg")}>{mark}</span>
          ) : null}
          {ln.origin === "ai" ? <span className="ml-2 text-[13px] text-muted">AI가 다듬음</span> : null}
          {flagged ? <span className="ml-2 text-[13px] text-muted">검사 경고</span> : null}
        </span>
      </button>
      {open && !editing ? (
        <div className="mt-1 mb-2 flex flex-wrap gap-1 pl-4" role="group" aria-label="줄">
          <Button size="sm" variant="ghost" disabled={busy || index === 0} onClick={() => onRun([{ op: "move_line", line_id: ln.id, index: index - 1 }])}>
            <ArrowUp size={14} /> 위로
          </Button>
          <Button size="sm" variant="ghost" disabled={busy || index === count - 1} onClick={() => onRun([{ op: "move_line", line_id: ln.id, index: index + 1 }])}>
            <ArrowDown size={14} /> 아래로
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            수정
          </Button>
          {ln.state === "stale" ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => onRun([{ op: "refresh_line", line_id: ln.id }])}>
              <RefreshCw size={14} /> 새 문장으로
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" disabled={suggest.isPending} onClick={() => suggest.mutate()}>
            다듬기 제안
          </Button>
          <Button size="sm" variant="ghost" className="text-danger" disabled={busy} onClick={() => onRun([{ op: "remove_line", line_id: ln.id }])}>
            <Trash2 size={14} /> 빼기
          </Button>
        </div>
      ) : null}
      {editing ? (
        <form
          className="mt-1 mb-2 space-y-2 pl-4"
          onSubmit={(e) => {
            e.preventDefault();
            onRun([{ op: "update_line", line_id: ln.id, text: text.trim() }]);
            setEditing(false);
          }}
        >
          <Textarea aria-label="줄 수정" rows={2} maxLength={500} value={text} onChange={(e) => setText(e.target.value)} />
          {ln.claim_id ? (
            <p className="text-[13px] text-muted">근거 사실과의 연결은 남습니다. 사실에 없는 수치·역할을 넣으면 검사에서 알려 줍니다.</p>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="primary" disabled={busy || !text.trim()}>
              저장
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              취소
            </Button>
          </div>
        </form>
      ) : null}
      {suggest.error ? <p className="pl-4 text-[13px] text-danger">{errText(suggest.error)}</p> : null}
      {suggestion ? (
        <div className="mt-1 mb-2 border-l-2 border-line pl-3 text-[13px]">
          <p className="text-sm leading-6">{suggestion.suggested_text}</p>
          {suggestion.status === "rejected" ? (
            <p className="text-muted">확인한 사실에 없는 내용이 들어가 적용하지 않습니다.</p>
          ) : (
            <div className="mt-1 flex gap-2">
              <Button
                size="sm"
                disabled={busy}
                onClick={() => {
                  onRun([{ op: "update_line", line_id: ln.id, text: suggestion.suggested_text, origin: "ai" }]);
                  setSuggestion(null);
                }}
              >
                적용
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSuggestion(null)}>
                버리기
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------- 내보내기

const FORMATS: { fmt: ResumeFormat; note: string }[] = [
  { fmt: "html", note: "미리보기와 같은 파일입니다. 브라우저로 열어 인쇄하면 같은 쪽 나눔으로 나옵니다." },
  { fmt: "docx", note: "Word에서 열고 고칠 수 있습니다. 쪽 나눔은 Word가 합니다." },
  { fmt: "md", note: "글만 담은 파일입니다." },
  { fmt: "json", note: "다른 이력서 도구와 주고받는 형식입니다. 사진은 넣지 않습니다." },
];

function ExportResumeSheet({
  versionId,
  onClose,
  onPrint,
}: {
  versionId: string;
  onClose: () => void;
  onPrint: () => void;
}) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const get = useMutation({
    mutationFn: async (fmt: ResumeFormat) => {
      const check = await api.resumeExportCheck(versionId, fmt);
      const file = await api.resumeExportFile(versionId, fmt);
      return { check, file };
    },
    onSuccess: ({ check, file }) => {
      setFindings(check.findings as Finding[]);
      saveBlob(file.blob, file.filename);
    },
  });
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title="내보내기" description="블라인드 판본은 모든 파일에서 사진·생년월일·주소·학교 이름을 뺍니다.">
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
        {FORMATS.map(({ fmt, note }) => (
          <li key={fmt} className="flex items-start gap-3 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{FORMAT_LABEL[fmt]}</p>
              <p className="text-[13px] text-muted">{note}</p>
            </div>
            <Button size="sm" disabled={get.isPending} onClick={() => get.mutate(fmt)}>
              <Download size={14} /> 받기
            </Button>
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
