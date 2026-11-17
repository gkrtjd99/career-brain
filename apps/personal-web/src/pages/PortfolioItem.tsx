import type {
  PortfolioImage,
  PortfolioImageKind,
  PortfolioItem,
  PortfolioLine,
  PortfolioLink,
  PortfolioLinkKind,
  PortfolioOperation,
  PortfolioSection,
  PortfolioVisibility,
} from "@career-brain/client";
import { PORTFOLIO_LINK_KINDS, PORTFOLIO_VISIBILITIES } from "@career-brain/client";
import { Button, cn, ConfirmDialog, FilterChips, IconButton, Input, Label, Select, SelectItem, Sheet, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, ImagePlus, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorText, Loaded, PageHeader } from "../components/common";
import { GitHubImportSheet } from "../components/portfolio/GitHubImportSheet";
import { api } from "../lib/api";
import { decodeErrorText, ShrinkFailed, shrinkImage } from "../lib/imageShrink";
import { formatKB } from "../lib/photo";
import {
  IMAGE_KIND_LABEL,
  isConflict,
  LINE_STATE_LABEL,
  LINK_KIND_LABEL,
  MAX_EDGE,
  MAX_IMAGE_BYTES,
  portfolioErrorText,
  VISIBILITY_LABEL,
  VISIBILITY_NOTE,
} from "../lib/portfolio";
import { portfolioItemRoute } from "../router";

const errText = (e: unknown) => portfolioErrorText(e) ?? errorText(e);

const SECTION_TITLE: Record<PortfolioSection, string> = { actions: "한 일", results: "결과" };

/** 항목 하나: 설정, 문제 → 내 역할 → 한 일 → 결과, 링크, 이미지. 결과·한 일 줄은 확인한 사실에서 고릅니다. */
export function PortfolioItemPage() {
  const { itemId } = portfolioItemRoute.useParams();
  const q = useQuery({ queryKey: ["portfolio", "item", itemId], queryFn: () => api.portfolioItem(itemId) });
  return <Loaded query={q}>{(v) => <Editor key={v.summary.id} v={v} />}</Loaded>;
}

function Editor({ v }: { v: PortfolioItem }) {
  const id = v.summary.id;
  const qc = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [picking, setPicking] = useState<PortfolioSection | null>(null);
  const [importing, setImporting] = useState(false);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["portfolio"] });
  const save = useMutation({
    mutationFn: (ops: PortfolioOperation[]) => api.savePortfolioItem(id, { base_revision: v.revision_no, operations: ops }),
    onSuccess: () => {
      setNotice(null);
      setPicking(null);
      refresh();
    },
    onError: (e) => {
      setNotice(errText(e));
      if (isConflict(e)) refresh();
    },
  });
  const patch = useMutation({
    mutationFn: (body: Parameters<typeof api.patchPortfolioItem>[1]) => api.patchPortfolioItem(id, body),
    onSuccess: () => {
      setNotice(null);
      refresh();
    },
    onError: (e) => setNotice(errText(e)),
  });
  const run = (ops: PortfolioOperation[]) => save.mutate(ops);
  const busy = save.isPending;
  const exp = v.experience;

  return (
    <div className="max-w-3xl">
      <PageHeader
        back={{ to: "/brain/portfolio", label: "포트폴리오" }}
        title={v.summary.title}
        subtitle={
          <span className="tabular-nums">
            {exp.id ? (
              <Link
                to="/brain/$experienceId"
                params={{ experienceId: exp.id }}
                className="underline underline-offset-2 hover:text-fg"
              >
                {exp.title}
              </Link>
            ) : (
              "경험 없음"
            )}
            {exp.active ? "" : " · 보관한 경험"}
            {[exp.organization, exp.period].filter(Boolean).map((x) => ` · ${x}`)}
          </span>
        }
        actions={
          <>
            <Button size="sm" onClick={() => setImporting(true)}>
              GitHub에서 가져오기
            </Button>
            <DeleteItem v={v} />
          </>
        }
      />
      {notice ? (
        <p role="alert" className="mb-4 text-[13px] text-danger">
          {notice}
        </p>
      ) : null}

      <Settings v={v} busy={patch.isPending} onPatch={(b) => patch.mutate(b)} />

      <TextField
        key={`problem-${v.revision_no}`}
        label="문제"
        hint="무엇이 문제였는지 적습니다. 직접 쓴 글이라 근거 없음으로 보입니다."
        value={v.content.problem}
        busy={busy}
        onSave={(text) => run([{ op: "set_text", field: "problem", text }])}
      />
      <TextField
        key={`role-${v.revision_no}`}
        label="내 역할"
        hint="내가 맡은 몫을 적습니다. 직접 쓴 글이라 근거 없음으로 보입니다."
        value={v.content.role}
        busy={busy}
        onSave={(text) => run([{ op: "set_text", field: "role", text }])}
      />
      {(["actions", "results"] as const).map((sec) => (
        <Lines key={sec} sec={sec} lines={v.content[sec]} busy={busy} onRun={run} onPick={() => setPicking(sec)} />
      ))}

      <Links v={v} busy={patch.isPending} onPatch={(links) => patch.mutate({ links })} />
      <Images v={v} />

      {picking ? (
        <ClaimPickSheet
          itemId={id}
          section={picking}
          busy={busy}
          error={save.error ? errText(save.error) : null}
          onClose={() => setPicking(null)}
          onRun={run}
        />
      ) : null}
      {importing ? <GitHubImportSheet itemId={id} onClose={() => setImporting(false)} /> : null}
    </div>
  );
}

function Block({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={title} className="mb-8">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line pb-1.5">
        <h2 className="min-w-0 flex-1 text-[15px] font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- 설정

function Settings({
  v,
  busy,
  onPatch,
}: {
  v: PortfolioItem;
  busy: boolean;
  onPatch: (b: { title?: string; visibility?: PortfolioVisibility; tags?: string[] }) => void;
}) {
  const [title, setTitle] = useState(v.summary.title);
  const [tags, setTags] = useState(v.summary.tags.join(", "));
  const vis = v.summary.visibility;
  return (
    <Block title="설정">
      <div className="space-y-4 pt-3">
        <div>
          <Label htmlFor="pi-title" className="mb-1">
            제목
          </Label>
          <Input
            id="pi-title"
            value={title}
            maxLength={200}
            disabled={busy}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title.trim() && title.trim() !== v.summary.title && onPatch({ title: title.trim() })}
          />
        </div>
        <div>
          <p className="mb-1.5 text-sm font-medium">공개 범위</p>
          <FilterChips
            label="공개 범위"
            value={vis}
            onChange={(x) => x !== vis && onPatch({ visibility: x })}
            items={PORTFOLIO_VISIBILITIES.map((x) => ({ value: x, label: VISIBILITY_LABEL[x] }))}
          />
          <p className="mt-1.5 text-[13px] text-muted">{VISIBILITY_NOTE[vis]}</p>
        </div>
        <div>
          <Label htmlFor="pi-tags" className="mb-1">
            기술 태그
          </Label>
          <Input
            id="pi-tags"
            value={tags}
            disabled={busy}
            placeholder="쉼표로 나눕니다 (예: Python, Redis)"
            onChange={(e) => setTags(e.target.value)}
            onBlur={() => {
              const next = tags
                .split(",")
                .map((t) => t.trim())
                .filter(Boolean);
              if (next.join("\u0000") !== v.summary.tags.join("\u0000")) onPatch({ tags: next });
            }}
          />
        </div>
      </div>
    </Block>
  );
}

// ---------------------------------------------------------------- 글·줄

function TextField({
  label,
  hint,
  value,
  busy,
  onSave,
}: {
  label: string;
  hint: string;
  value: string | null;
  busy: boolean;
  onSave: (text: string | null) => void;
}) {
  const [text, setText] = useState(value ?? "");
  const dirty = text.trim() !== (value ?? "").trim();
  return (
    <Block title={label} aside={value ? <span className="text-[13px] text-muted">근거 없음</span> : null}>
      <div className="pt-3">
        <Textarea aria-label={label} rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy || !dirty} onClick={() => onSave(text.trim() || null)}>
            저장
          </Button>
          <span className="text-[13px] text-muted">{hint}</span>
        </div>
      </div>
    </Block>
  );
}

function Lines({
  sec,
  lines,
  busy,
  onRun,
  onPick,
}: {
  sec: PortfolioSection;
  lines: PortfolioLine[];
  busy: boolean;
  onRun: (ops: PortfolioOperation[]) => void;
  onPick: () => void;
}) {
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState("");
  const title = SECTION_TITLE[sec];
  return (
    <Block
      title={title}
      aside={
        <>
          <span className="text-[13px] text-muted tabular-nums">{lines.length}</span>
          <Button size="sm" variant="ghost" onClick={onPick}>
            <Plus size={14} /> 확인한 사실에서
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setWriting(true)}>
            직접 쓰기
          </Button>
        </>
      }
    >
      {lines.length ? (
        <ul className="divide-y divide-line">
          {lines.map((ln, i) => (
            <LineRow key={ln.id} ln={ln} index={i} count={lines.length} busy={busy} onRun={onRun} />
          ))}
        </ul>
      ) : (
        <p className="py-3 text-[13px] text-muted">
          {sec === "results" ? "결과 줄이 없습니다. 확인한 사실에서 고르면 근거가 이어집니다." : "한 일이 없습니다. 확인한 사실에서 고르면 근거가 이어집니다."}
        </p>
      )}
      {writing ? (
        <form
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            onRun([{ op: "add_line", section: sec, text: text.trim() }]);
            setText("");
            setWriting(false);
          }}
        >
          <Textarea aria-label={`${title} 줄 쓰기`} rows={2} maxLength={500} value={text} onChange={(e) => setText(e.target.value)} />
          <p className="text-[13px] text-muted">직접 쓴 줄은 근거 없음으로 보입니다.</p>
          <div className="flex gap-2">
            <Button type="submit" size="sm" variant="primary" disabled={busy || !text.trim()}>
              넣기
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setWriting(false)}>
              취소
            </Button>
          </div>
        </form>
      ) : null}
    </Block>
  );
}

function LineRow({
  ln,
  index,
  count,
  busy,
  onRun,
}: {
  ln: PortfolioLine;
  index: number;
  count: number;
  busy: boolean;
  onRun: (ops: PortfolioOperation[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(ln.text);
  const mark = LINE_STATE_LABEL[ln.state];
  return (
    <li className="py-1 text-sm leading-6">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="-mx-2 flex min-h-9 w-[calc(100%+1rem)] items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-150 hover:bg-subtle/60"
      >
        <span aria-hidden className="text-muted">
          ·
        </span>
        <span className="min-w-0 flex-1 break-words">
          {ln.text}
          {mark ? (
            <span className={cn("ml-2 text-[13px] text-muted", ln.state === "broken" && "font-semibold text-fg")}>{mark}</span>
          ) : null}
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
            <p className="text-[13px] text-muted">근거 사실과의 연결은 남습니다. 사실에 없는 수치·역할은 넣지 않습니다.</p>
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
    </li>
  );
}

function ClaimPickSheet({
  itemId,
  section,
  busy,
  error,
  onClose,
  onRun,
}: {
  itemId: string;
  section: PortfolioSection;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onRun: (ops: PortfolioOperation[]) => void;
}) {
  const q = useQuery({ queryKey: ["portfolio", "picker", itemId], queryFn: () => api.portfolioPicker(itemId) });
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${SECTION_TITLE[section]} 줄 더하기`}
      description="이 경험과 그 아래 경험의 확인한 사실입니다. 고른 줄은 근거가 이어집니다."
    >
      <Loaded query={q}>
        {(p) =>
          p.claims.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {p.claims.map((c) => (
                <li key={c.id} className="flex items-start gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm break-words">{c.statement}</p>
                    <p className="text-[13px] text-muted">{c.experience_title}</p>
                  </div>
                  {c.used ? (
                    <span className="shrink-0 py-2 text-[13px] text-muted">넣음</span>
                  ) : (
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => onRun([{ op: "add_line", section, claim_id: c.id }])}
                    >
                      넣기
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-muted">
              이 경험에 확인한 사실이 아직 없습니다. 경험 화면에서 사실을 더하거나 받은 자료함의 후보를 확인합니다.
            </p>
          )
        }
      </Loaded>
      {error ? <p className="mt-3 text-[13px] text-danger">{error}</p> : null}
    </Sheet>
  );
}

// ---------------------------------------------------------------- 링크

const day = (x?: string | null) => (x ? x.slice(0, 10).replaceAll("-", ".") : null);

function Links({
  v,
  busy,
  onPatch,
}: {
  v: PortfolioItem;
  busy: boolean;
  onPatch: (links: { id?: string; kind: PortfolioLinkKind; url: string; label?: string | null }[]) => void;
}) {
  const links = v.summary.links;
  const [kind, setKind] = useState<PortfolioLinkKind>("repo");
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const plain = (lk: PortfolioLink) => ({ id: lk.id, kind: lk.kind, url: lk.url, label: lk.label });
  return (
    <Block title="링크" aside={<span className="text-[13px] text-muted tabular-nums">{links.length}</span>}>
      {links.length ? (
        <ul className="divide-y divide-line">
          {links.map((lk) => {
            const info = lk.info
              ? [lk.info.language, day(lk.info.created_at) && `만든 날 ${day(lk.info.created_at)}`, day(lk.info.pushed_at) && `마지막 push ${day(lk.info.pushed_at)}`]
                  .filter(Boolean)
                  .join(" · ")
              : null;
            return (
              <li key={lk.id} className="flex items-start gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    <span className="text-muted">{lk.label || LINK_KIND_LABEL[lk.kind]}</span>{" "}
                    <span className="break-all">{lk.url}</span>
                  </p>
                  {info ? <p className="text-[13px] text-muted tabular-nums">{info} · 참고 정보</p> : null}
                </div>
                <IconButton
                 
                 
                  label="링크 빼기"
                 
                  disabled={busy}
                  onClick={() => onPatch(links.filter((x) => x.id !== lk.id).map(plain))}
                >
                  <Trash2 size={15} />
                </IconButton>
              </li>
            );
          })}
        </ul>
      ) : null}
      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          onPatch([...links.map(plain), { kind, url: url.trim(), label: label.trim() || null }]);
          setUrl("");
          setLabel("");
        }}
      >
        <div>
          <Label htmlFor="lk-kind" className="mb-1">
            종류
          </Label>
          <Select id="lk-kind" value={kind} onValueChange={(v) => setKind(v as PortfolioLinkKind)}>
            {PORTFOLIO_LINK_KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {LINK_KIND_LABEL[k]}
              </SelectItem>
            ))}
          </Select>
        </div>
        <div className="min-w-0 flex-[2_1_14rem]">
          <Label htmlFor="lk-url" className="mb-1">
            주소
          </Label>
          <Input id="lk-url" value={url} inputMode="url" placeholder="https://" maxLength={500} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <div className="min-w-0 flex-[1_1_8rem]">
          <Label htmlFor="lk-label" className="mb-1">
            이름
          </Label>
          <Input id="lk-label" value={label} maxLength={80} placeholder="비워도 됩니다" onChange={(e) => setLabel(e.target.value)} />
        </div>
        <Button type="submit" disabled={busy || !url.trim()}>
          더하기
        </Button>
      </form>
      <p className="mt-2 text-[13px] text-muted">내보낸 파일에는 주소를 글로만 적습니다.</p>
    </Block>
  );
}

// ---------------------------------------------------------------- 이미지

function Images({ v }: { v: PortfolioItem }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<PortfolioImageKind>("screenshot");
  const upload = useMutation({
    mutationFn: async (file: File) => {
      setStatus("이 브라우저에서 줄이는 중");
      let shrunk;
      try {
        shrunk = await shrinkImage(file, { maxEdge: MAX_EDGE, maxBytes: MAX_IMAGE_BYTES });
      } catch (e) {
        throw new Error(
          e instanceof ShrinkFailed
            ? e.message === "too_small"
              ? "이미지가 너무 작습니다 (한 변 16px 이상)."
              : "1MB 안으로 줄이지 못했습니다. 더 작은 이미지를 고릅니다."
            : decodeErrorText(file),
        );
      }
      setStatus("올리는 중");
      const ext = shrunk.mime === "image/png" ? "png" : "jpg";
      const r = await api.uploadPortfolioImage(v.summary.id, shrunk.blob, {
        kind,
        visibility: "private",
        filename: `image.${ext}`,
      });
      return { r, shrunk };
    },
    onSuccess: ({ r, shrunk }) => {
      setError(null);
      setStatus(
        r.deduplicated
          ? "같은 이미지가 이미 있습니다."
          : `${shrunk.from.width}×${shrunk.from.height} → ${shrunk.width}×${shrunk.height}, ${formatKB(r.bytes)}로 줄여 넣었습니다. 위치·카메라 정보는 삭제했습니다.`,
      );
      void qc.invalidateQueries({ queryKey: ["portfolio"] });
    },
    onError: (e) => {
      setStatus(null);
      setError(e instanceof Error && !("status" in e) ? e.message : errText(e));
    },
  });
  return (
    <Block
      title="이미지"
      aside={<span className="text-[13px] text-muted tabular-nums">{v.images.length}/12</span>}
    >
      <p className="pt-3 text-[13px] text-muted">
        올린 이미지는 '나만'으로 시작합니다. 회사 내부 화면이 섞이지 않았는지 보고 공개 범위를 바꿉니다. 이미지는
        모델로 보내지 않습니다.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Select aria-label="이미지 종류" value={kind} onValueChange={(v) => setKind(v as PortfolioImageKind)} className="w-auto">
          {(Object.keys(IMAGE_KIND_LABEL) as PortfolioImageKind[]).map((k) => (
            <SelectItem key={k} value={k}>
              {IMAGE_KIND_LABEL[k]}
            </SelectItem>
          ))}
        </Select>
        <Button disabled={upload.isPending || v.images.length >= 12} onClick={() => input.current?.click()}>
          <ImagePlus size={15} /> 이미지 올리기
        </Button>
        <input
          ref={input}
          type="file"
          accept="image/*"
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) upload.mutate(f);
          }}
        />
      </div>
      {status ? (
        <p role="status" className="mt-2 text-[13px] text-muted tabular-nums">
          {status}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-[13px] text-danger">
          {error}
        </p>
      ) : null}
      {v.images.length ? (
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {v.images.map((img, i) => (
            <ImageRow key={img.id} img={img} index={i} count={v.images.length} itemVisibility={v.summary.visibility} />
          ))}
        </ul>
      ) : null}
    </Block>
  );
}

function useObjectUrl(imageId: string, sha: string) {
  const q = useQuery({
    queryKey: ["portfolio", "image-file", imageId, sha],
    queryFn: () => api.portfolioImageFile(imageId),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const u = URL.createObjectURL(q.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [q.data]);
  return url;
}

function ImageRow({
  img,
  index,
  count,
  itemVisibility,
}: {
  img: PortfolioImage;
  index: number;
  count: number;
  itemVisibility: PortfolioVisibility;
}) {
  const qc = useQueryClient();
  const url = useObjectUrl(img.id, img.sha256);
  const [caption, setCaption] = useState(img.caption ?? "");
  const patch = useMutation({
    mutationFn: (body: Parameters<typeof api.patchPortfolioImage>[1]) => api.patchPortfolioImage(img.id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["portfolio"] }),
  });
  const del = useMutation({
    mutationFn: () => api.deletePortfolioImage(img.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["portfolio"] }),
  });
  const busy = patch.isPending || del.isPending;
  const capped = itemVisibility === "private" || (itemVisibility === "application" && img.visibility === "public");
  return (
    <li className="flex flex-col gap-3 py-3 sm:flex-row sm:items-start">
      <div className="flex h-28 w-full shrink-0 items-center justify-center overflow-hidden rounded-md bg-subtle sm:w-40">
        {url ? (
          <img src={url} alt={img.caption ?? IMAGE_KIND_LABEL[img.kind]} className="max-h-28 max-w-full object-contain" />
        ) : null}
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-[13px] text-muted tabular-nums">
          {IMAGE_KIND_LABEL[img.kind]} · {img.width_px}×{img.height_px} · {formatKB(img.bytes)}
        </p>
        <Input
          aria-label="이미지 설명"
          value={caption}
          maxLength={200}
          placeholder="설명 (출력에 함께 나갑니다)"
          disabled={busy}
          onChange={(e) => setCaption(e.target.value)}
          onBlur={() => caption.trim() !== (img.caption ?? "") && patch.mutate({ caption: caption.trim() || null })}
        />
        <FilterChips
          label="이미지 공개 범위"
          value={img.visibility}
          onChange={(x) => x !== img.visibility && patch.mutate({ visibility: x })}
          items={PORTFOLIO_VISIBILITIES.map((x) => ({ value: x, label: VISIBILITY_LABEL[x] }))}
        />
        {capped ? (
          <p className="text-[13px] text-muted">
            항목이 '{VISIBILITY_LABEL[itemVisibility]}'이라 이 이미지도 그 범위 안에서만 나갑니다.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-1">
          <Button size="sm" variant="ghost" disabled={busy || index === 0} onClick={() => patch.mutate({ position: index - 1 })}>
            <ArrowUp size={14} /> 앞으로
          </Button>
          <Button size="sm" variant="ghost" disabled={busy || index === count - 1} onClick={() => patch.mutate({ position: index + 1 })}>
            <ArrowDown size={14} /> 뒤로
          </Button>
          <Button size="sm" variant="ghost" className="text-danger" disabled={busy} onClick={() => del.mutate()}>
            <Trash2 size={14} /> 삭제
          </Button>
        </div>
        {patch.error || del.error ? (
          <p role="alert" className="text-[13px] text-danger">
            {errText(patch.error ?? del.error)}
          </p>
        ) : null}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------- 삭제

function DeleteItem({ v }: { v: PortfolioItem }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const del = useMutation({
    mutationFn: () => api.deletePortfolioItem(v.summary.id),
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
        title="항목을 삭제할까요?"
        description={`'${v.summary.title}'의 글·줄·이미지가 모두 사라지고, 넣어 둔 묶음 ${v.summary.bundles}개에서도 빠집니다. 되돌릴 수 없습니다. 경험과 확인한 사실은 그대로입니다.`}
        confirmLabel="삭제"
        tone="danger"
        busy={del.isPending}
        onConfirm={() => del.mutate()}
      >
        {del.error ? <p className="text-[13px] text-danger">{errText(del.error)}</p> : null}
      </ConfirmDialog>
    </>
  );
}
