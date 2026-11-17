import type { Company, CompanyFact, CompanyPage as CompanySourcePage } from "@career-brain/client";
import { Button, cn, ConfirmDialog, Input, Label, Section, Sheet, Spinner, Textarea } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ExternalLink, Pencil, Plus, Trash2 } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { CompanyForm, companyValues, toCompanyBody } from "../components/companies/CompanyForm";
import { EMPTY_FACT, FactForm, type FactFormValues } from "../components/companies/FactForm";
import { ErrorText, Loaded, PageHeader, Tabs } from "../components/common";
import { api } from "../lib/api";
import { asOfLabel, FACT_KIND_LABEL, FACT_KIND_ORDER, pageName, pageStatusLabel } from "../lib/companies";
import { formatDate, formatDeadline } from "../lib/dates";
import { hostOf } from "../lib/format";
import { APP_STATUS, POSTING_STATUS } from "../lib/labels";

const route = getRouteApi("/brain/companies/$companyId");
const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";

export function CompanyPage() {
  const { companyId } = route.useParams();
  const q = useQuery({
    queryKey: ["company", companyId],
    queryFn: () => api.company(companyId),
    refetchInterval: (query) => (query.state.data?.pages.some((p) => p.status === "pending") ? 2500 : false),
  });
  return <Loaded query={q}>{(c) => <CompanyView c={c} />}</Loaded>;
}

function useInvalidate(companyId: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["company", companyId] });
    qc.invalidateQueries({ queryKey: ["companies"] });
  };
}

function CompanyView({ c }: { c: Company }) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(false);
  const [pageId, setPageId] = useState<string | null>(null);
  const candidates = c.facts.filter((f) => f.status === "candidate");
  const confirmed = c.facts.filter((f) => f.status === "confirmed");
  const sub = [c.aliases.join(", ")].filter(Boolean);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        back={{ to: "/brain/companies", label: "회사" }}
        title={c.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5">
            {sub.map((s) => (
              <span key={s}>{s}</span>
            ))}
            {sub.length && c.homepage ? <span aria-hidden>·</span> : null}
            {c.homepage ? (
              <a
                href={c.homepage}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-0.5 underline-offset-2 hover:text-fg hover:underline"
              >
                {hostOf(c.homepage)} <ExternalLink size={12} />
              </a>
            ) : null}
            {sub.length || c.homepage ? <span aria-hidden>·</span> : null}
            <span>참고 자료라 문서 근거로 쓰지 않습니다.</span>
          </span>
        }
        actions={
          <>
            <Button onClick={() => setEditing(true)}>
              <Pencil size={14} /> 수정
            </Button>
            <Button variant="primary" onClick={() => setAdding(true)}>
              <Plus size={15} /> 원문 추가
            </Button>
          </>
        }
      />

      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-14">
        <div className="min-w-0 space-y-10">
          {c.facts.length === 0 ? (
            <p className="border-t border-line pt-3 text-sm text-muted">
              {c.pages.length
                ? "아직 항목이 없습니다. 오른쪽 원문을 열어 인용할 글을 고르면 항목으로 추가됩니다."
                : "아직 항목이 없습니다. 회사 소개·채용·IR 페이지 주소를 넣거나 글을 붙여넣으면 인재상·주요 사업 같은 항목 후보를 원문 인용과 함께 보여 줍니다."}{" "}
              {c.pages.length ? null : (
                <button type="button" onClick={() => setAdding(true)} className="text-fg underline underline-offset-2">
                  원문 추가
                </button>
              )}
            </p>
          ) : null}

          {candidates.length ? (
            <section>
              <h2 className="text-[15px] font-semibold">
                확인할 항목 <span className="font-normal text-muted tabular-nums">{candidates.length}</span>
              </h2>
              <p className="mt-0.5 text-[13px] text-muted">모델이 원문에서 고른 후보입니다. 인용이 맞으면 확인합니다.</p>
              <FactList facts={candidates} c={c} showKind onOpenPage={setPageId} />
            </section>
          ) : null}

          {FACT_KIND_ORDER.map((kind) => {
            const facts = confirmed.filter((f) => f.kind === kind);
            return facts.length ? (
              <section key={kind}>
                <h2 className="text-[15px] font-semibold">
                  {FACT_KIND_LABEL[kind]} <span className="font-normal text-muted tabular-nums">{facts.length}</span>
                </h2>
                <FactList facts={facts} c={c} onOpenPage={setPageId} />
              </section>
            ) : null;
          })}
        </div>

        <aside className="mt-10 min-w-0 space-y-10 lg:mt-0">
          <Section
            title="원문"
            action={
              <Button size="sm" variant="ghost" onClick={() => setAdding(true)} aria-label="원문 추가">
                <Plus size={14} /> 추가
              </Button>
            }
          >
            <Pages c={c} onOpen={setPageId} />
          </Section>
          <Section title="이 회사 공고">
            <Postings c={c} />
          </Section>
          {c.applications.length ? (
            <Section title="지원 이력">
              <ul className="divide-y divide-line border-y border-line">
                {c.applications.map((a) => (
                  <li key={a.id}>
                    <Link
                      to="/applications/$applicationId"
                      params={{ applicationId: a.id }}
                      className={cn(ROW, "block")}
                    >
                      <div className="truncate text-sm font-medium">{a.title}</div>
                      <div className="text-[13px] text-muted tabular-nums">
                        {[APP_STATUS[a.status as keyof typeof APP_STATUS]?.label ?? a.status, a.submitted_at ? `제출 ${formatDate(a.submitted_at)}` : null]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
        </aside>
      </div>

      <AddSourceSheet companyId={c.id} open={adding} onClose={() => setAdding(false)} />
      <EditCompanySheet c={c} open={editing} onClose={() => setEditing(false)} />
      <PageSheet companyId={c.id} pageId={pageId} onClose={() => setPageId(null)} />
    </div>
  );
}

// ---------------------------------------------------------------- 항목

function FactList({
  facts,
  c,
  showKind = false,
  onOpenPage,
}: {
  facts: CompanyFact[];
  c: Company;
  showKind?: boolean;
  onOpenPage: (pageId: string) => void;
}) {
  return (
    <ul className="mt-3 divide-y divide-line border-y border-line">
      {facts.map((f) => (
        <FactItem key={f.id} f={f} c={c} showKind={showKind} onOpenPage={onOpenPage} />
      ))}
    </ul>
  );
}

function FactItem({
  f,
  c,
  showKind,
  onOpenPage,
}: {
  f: CompanyFact;
  c: Company;
  showKind: boolean;
  onOpenPage: (pageId: string) => void;
}) {
  const invalidate = useInvalidate(c.id);
  const [editing, setEditing] = useState(false);
  const confirm = useMutation({ mutationFn: () => api.confirmCompanyFact(c.id, f.id), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: () => api.deleteCompanyFact(c.id, f.id), onSuccess: invalidate });
  const save = useMutation({
    mutationFn: (v: FactFormValues) =>
      api.updateCompanyFact(c.id, f.id, {
        kind: v.kind,
        body: v.body.trim(),
        quote: v.quote.trim(),
        as_of: v.as_of.trim() || null,
      }),
    onSuccess: () => {
      invalidate();
      setEditing(false);
    },
  });
  const page = c.pages.find((p) => p.id === f.citation.page_id);
  const asOf = asOfLabel(f.as_of);
  const source = f.citation.source_url;

  return (
    <li className="py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-6 break-words">{f.body}</p>
          <blockquote className="mt-1 border-l border-line pl-2.5 text-[13px] leading-5 break-words text-muted">
            {f.citation.quote}
          </blockquote>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-[13px] text-muted tabular-nums">
            {showKind ? (
              <>
                <span className="text-fg">{FACT_KIND_LABEL[f.kind]}</span>
                <span aria-hidden>·</span>
              </>
            ) : null}
            {asOf ? (
              <>
                <span>기준 {asOf}</span>
                <span aria-hidden>·</span>
              </>
            ) : null}
            <span>가져온 날 {formatDate(f.citation.fetched_at)}</span>
            <span aria-hidden>·</span>
            {source ? (
              <a
                href={source}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex min-h-9 items-center gap-0.5 underline-offset-2 hover:text-fg hover:underline sm:min-h-6"
              >
                {hostOf(source)} <ExternalLink size={12} />
              </a>
            ) : (
              <button
                type="button"
                onClick={() => onOpenPage(f.citation.page_id)}
                className="inline-flex min-h-9 items-center underline-offset-2 hover:text-fg hover:underline sm:min-h-6"
              >
                {page ? pageName(page) : "붙여넣은 글"}
              </button>
            )}
            {f.stale ? (
              <>
                <span aria-hidden>·</span>
                <span className="font-semibold text-fg" title="기준일이나 가져온 날이 1년이 넘었습니다">
                  오래됨
                </span>
              </>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {f.status === "candidate" ? (
            <Button size="sm" className="max-sm:h-9" onClick={() => confirm.mutate()} disabled={confirm.isPending}>
              확인
            </Button>
          ) : null}
          <Button size="icon" variant="ghost" aria-label="항목 수정" onClick={() => setEditing(true)}>
            <Pencil size={14} />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="항목 삭제"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      </div>
      <ErrorText error={confirm.error ?? remove.error} className="mt-1" />
      <Sheet open={editing} onOpenChange={(o) => !o && setEditing(false)} title="항목 수정">
        {editing ? (
          <FactForm
            initial={{ kind: f.kind, quote: f.citation.quote, body: f.body, as_of: f.as_of ?? "" }}
            submitLabel="저장"
            pending={save.isPending}
            error={save.error}
            onSubmit={(v) => save.mutate(v)}
            onCancel={() => setEditing(false)}
          />
        ) : null}
      </Sheet>
    </li>
  );
}

// ---------------------------------------------------------------- 원문

function Pages({ c, onOpen }: { c: Company; onOpen: (pageId: string) => void }) {
  if (!c.pages.length)
    return <p className="border-t border-line pt-3 text-[13px] text-muted">페이지 주소나 글을 넣으면 여기에 쌓입니다.</p>;
  return (
    <ul className="divide-y divide-line border-y border-line">
      {c.pages.map((p) => (
        <li key={p.id}>
          <button
            type="button"
            onClick={() => p.status === "done" && onOpen(p.id)}
            disabled={p.status !== "done"}
            className={cn(ROW, "block w-full text-left disabled:hover:bg-transparent")}
          >
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{pageName(p)}</span>
              {p.status === "pending" ? <Spinner /> : null}
            </div>
            <div
              className={cn(
                "text-[13px] tabular-nums",
                p.status === "failed" ? "font-medium text-danger" : "text-muted",
              )}
            >
              {[p.fetched_at ? formatDate(p.fetched_at) : null, pageStatusLabel(p)].filter(Boolean).join(" · ")}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

function AddSourceSheet({ companyId, open, onClose }: { companyId: string; open: boolean; onClose: () => void }) {
  const invalidate = useInvalidate(companyId);
  const [mode, setMode] = useState<"url" | "paste">("url");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const done = () => {
    invalidate();
    setUrl("");
    setTitle("");
    setText("");
    onClose();
  };
  const byUrl = useMutation({ mutationFn: () => api.importCompanyUrl(companyId, url.trim()), onSuccess: done });
  const byPaste = useMutation({
    mutationFn: () => api.importCompanyPaste(companyId, text, title.trim()),
    onSuccess: done,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mode === "url" && url.trim()) byUrl.mutate();
    if (mode === "paste" && text.trim()) byPaste.mutate();
  };
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          byUrl.reset();
          byPaste.reset();
          onClose();
        }
      }}
      title="원문 추가"
      description="원문에서 고른 글만 항목이 됩니다. 모델이 있으면 항목 후보를 함께 뽑습니다."
    >
      <Tabs
        value={mode}
        onChange={setMode}
        items={[
          { value: "url", label: "페이지 주소" },
          { value: "paste", label: "글 붙여넣기" },
        ]}
      />
      <form onSubmit={submit} className="space-y-4">
        {mode === "url" ? (
          <div className="space-y-1.5">
            <Label htmlFor="company-url">페이지 주소</Label>
            <Input
              id="company-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              inputMode="url"
              placeholder="https://"
              maxLength={2000}
              autoFocus
            />
            <p className="text-[13px] text-muted">
              이 페이지 한 건만 가져옵니다. 로그인이 필요하거나 자동 요청을 막는 사이트는 글을 붙여넣으세요.
            </p>
          </div>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="company-paste-title">제목 (선택)</Label>
              <Input
                id="company-paste-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="예: 2024 사업보고서 요약"
                maxLength={300}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="company-paste-text">글</Label>
              <Textarea
                id="company-paste-text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={10}
                maxLength={60000}
              />
              <p className="text-[13px] text-muted">회사 소개, IR 자료, 기사처럼 공개된 글을 넣습니다.</p>
            </div>
          </>
        )}
        <ErrorText error={mode === "url" ? byUrl.error : byPaste.error} />
        <div className="flex gap-2">
          <Button
            type="submit"
            variant="primary"
            disabled={mode === "url" ? byUrl.isPending || !url.trim() : byPaste.isPending || !text.trim()}
          >
            {mode === "url" ? "가져오기" : byPaste.isPending ? "저장하는 중" : "저장"}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

/** 원문 한 건: 글을 끌어 선택하면 인용 칸에 들어간다. 사람이 직접 고른 항목은 바로 확인됨. */
function PageSheet({ companyId, pageId, onClose }: { companyId: string; pageId: string | null; onClose: () => void }) {
  const invalidate = useInvalidate(companyId);
  const textRef = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState("");
  const [formKey, setFormKey] = useState(0);
  const q = useQuery({
    queryKey: ["company-page", companyId, pageId],
    queryFn: () => api.companyPage(companyId, pageId!),
    enabled: !!pageId,
  });
  const add = useMutation({
    mutationFn: (v: FactFormValues) =>
      api.addCompanyFact(companyId, {
        page_id: pageId!,
        kind: v.kind,
        body: v.body.trim(),
        quote: v.quote.trim(),
        as_of: v.as_of.trim() || null,
      }),
    onSuccess: () => {
      invalidate();
      setPicked("");
      setFormKey((k) => k + 1);
    },
  });
  const pick = () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !textRef.current?.contains(sel.anchorNode)) return;
    const s = sel.toString().trim();
    if (s) setPicked(s.slice(0, 2000));
  };
  const close = () => {
    add.reset();
    setPicked("");
    onClose();
  };
  return (
    <Sheet
      open={!!pageId}
      onOpenChange={(o) => !o && close()}
      title={q.data ? pageName(q.data) : "원문"}
      description={
        q.data
          ? [q.data.fetched_at ? `가져온 날 ${formatDate(q.data.fetched_at)}` : null, q.data.final_url ?? q.data.url]
              .filter(Boolean)
              .join(" · ")
          : undefined
      }
      width="lg"
    >
      <Loaded query={q}>
        {(p: CompanySourcePage) => (
          <div className="space-y-6">
            <div>
              <p className="mb-2 text-[13px] text-muted">항목으로 남길 글을 끌어서 선택하면 아래 인용 칸에 들어갑니다.</p>
              <div
                ref={textRef}
                onMouseUp={pick}
                onKeyUp={pick}
                onTouchEnd={pick}
                className="max-h-[40dvh] overflow-y-auto border-y border-line py-3 text-sm leading-6 break-words whitespace-pre-wrap"
              >
                {p.text}
              </div>
            </div>
            <section>
              <h3 className="mb-3 text-[15px] font-semibold">항목 추가</h3>
              <FactForm
                key={formKey}
                initial={EMPTY_FACT}
                quote={picked}
                submitLabel={add.isPending ? "추가하는 중" : "항목 추가"}
                pending={add.isPending}
                error={add.error}
                onSubmit={(v) => add.mutate(v)}
              />
              {add.isSuccess ? <p className="mt-2 text-[13px] text-muted" role="status">항목을 추가했습니다.</p> : null}
            </section>
          </div>
        )}
      </Loaded>
    </Sheet>
  );
}

// ---------------------------------------------------------------- 공고

function Postings({ c }: { c: Company }) {
  const invalidate = useInvalidate(c.id);
  const qc = useQueryClient();
  const sugg = useQuery({
    queryKey: ["company", c.id, "posting-suggestions"],
    queryFn: () => api.companyPostingSuggestions(c.id),
  });
  const link = useMutation({
    mutationFn: (postingId: string) => api.linkCompanyPostings(c.id, [postingId]),
    onSuccess: (_r, postingId) => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["company", c.id, "posting-suggestions"] });
      qc.invalidateQueries({ queryKey: ["posting", postingId] });
    },
  });
  const suggestions = sugg.data ?? [];
  return (
    <div className="space-y-4">
      {c.postings.length ? (
        <ul className="divide-y divide-line border-y border-line">
          {c.postings.map((p) => (
            <li key={p.id}>
              <Link to="/postings/$postingId" params={{ postingId: p.id }} className={cn(ROW, "block")}>
                <div className="truncate text-sm font-medium">{p.title ?? "제목 없음"}</div>
                <div className="text-[13px] text-muted tabular-nums">
                  {[
                    POSTING_STATUS[p.status as keyof typeof POSTING_STATUS]?.label ?? p.status,
                    p.deadline ? formatDeadline(p.deadline) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="border-t border-line pt-3 text-[13px] text-muted">연결한 공고가 없습니다.</p>
      )}
      {suggestions.length ? (
        <div>
          <p className="text-[13px] text-muted">회사 이름이 같은 공고입니다. 같은 회사면 연결합니다.</p>
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {suggestions.map((s) => (
              <li key={s.posting_id} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{s.title ?? "제목 없음"}</div>
                  <div className="truncate text-[13px] text-muted">{s.company}</div>
                </div>
                <Button
                  size="sm"
                  className="max-sm:h-9"
                  onClick={() => link.mutate(s.posting_id)}
                  disabled={link.isPending}
                >
                  연결
                </Button>
              </li>
            ))}
          </ul>
          <ErrorText error={link.error} className="mt-1" />
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- 회사 수정·삭제

function EditCompanySheet({ c, open, onClose }: { c: Company; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const invalidate = useInvalidate(c.id);
  const [deleting, setDeleting] = useState(false);
  const save = useMutation({
    mutationFn: (body: ReturnType<typeof toCompanyBody>) => api.updateCompany(c.id, body),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteCompany(c.id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ["company", c.id] });
      qc.invalidateQueries({ queryKey: ["companies"] });
      qc.invalidateQueries({ queryKey: ["posting"] });
      navigate({ to: "/brain/companies" });
    },
  });
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          save.reset();
          onClose();
        }
      }}
      title="회사 수정"
    >
      {open ? (
        <div className="space-y-8">
          <CompanyForm
            initial={companyValues(c)}
            submitLabel="저장"
            pending={save.isPending}
            error={save.error}
            onSubmit={(v) => save.mutate(toCompanyBody(v))}
            onCancel={onClose}
          />
          <div className="border-t border-line pt-4">
            <Button variant="danger" onClick={() => setDeleting(true)}>
              <Trash2 size={14} /> 회사 삭제
            </Button>
          </div>
        </div>
      ) : null}
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`${c.name} 삭제`}
        description="이 회사의 원문과 항목을 모두 삭제합니다. 공고는 남고 회사 연결만 해제됩니다. 되돌릴 수 없습니다."
        confirmLabel={remove.isPending ? "삭제하는 중" : "삭제"}
        busy={remove.isPending}
        tone="danger"
        onConfirm={() => remove.mutate()}
      >
        <ErrorText error={remove.error} />
      </ConfirmDialog>
    </Sheet>
  );
}
