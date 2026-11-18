import type { SourceListItem, SourcePurpose } from "@career-brain/client";
import { Button, cn, Disclosure, FilterChips, Label, Select, SelectItem, toast, type BadgeTone } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ConfirmAllButton } from "../components/brain/ExperienceActions";
import { ErrorText, Loaded, PageHeader } from "../components/common";
import { ProofPanel } from "../components/credentials/ProofPanel";
import { CoverLetterAnswers, looksLikeCoverLetter } from "../components/sources/CoverLetterAnswers";
import { PurposeChoice } from "../components/sources/PurposeChoice";
import { api } from "../lib/api";
import { formatDate } from "../lib/dates";
import { hostOf } from "../lib/format";
import { parseFailNote, reasonText } from "../lib/ingest";
import {
  ATTENTION_LABEL,
  DATA_CLASS_LABEL,
  EXTRACTION_STATUS,
  PARSE_STATUS,
  PURPOSE_LABEL,
  PURPOSE_OPTIONS,
} from "../lib/labels";

const ROW = "-mx-2 rounded-md px-2 py-3 transition-colors duration-150 hover:bg-subtle/60";
const TEXT_LINK = "inline-flex min-h-9 items-center text-fg underline underline-offset-2 sm:min-h-6";

/** 배지 대신 글자로: 실패만 빨강, 손이 가야 하는 상태는 진하게, 나머지는 회색. */
export const TONE_TEXT: Record<BadgeTone, string> = {
  ok: "text-muted",
  none: "text-muted",
  neutral: "text-muted",
  accent: "text-muted",
  warn: "font-semibold",
  danger: "font-medium text-danger",
};

export function fileSize(n: number | null) {
  if (n === null) return null;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

const pending = (rows: SourceListItem[] | undefined) =>
  rows?.some((s) => s.parse_status === "pending" || s.extraction_status === "pending") ? 4000 : false;

/** 날짜를 앞에 둔다: 좁은 화면에서 긴 파일 이름이 잘려도 언제 넣은 자료인지 보이게 */
function sourceMeta(src: SourceListItem, withPurpose = true) {
  return [
    formatDate(src.created_at),
    withPurpose ? PURPOSE_LABEL[src.purpose] : null,
    fileSize(src.size_bytes),
    src.original_filename !== src.title ? src.original_filename : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * 브레인 색인의 받은 자료함: 손이 가야 하는 자료만 (읽기·추출 실패, 처리 중, 일부만, 확인할 후보, 추출 전).
 * 다 처리된 자료는 기록(경험·사실의 원문 근거)에서 찾아 들어간다. 파일 목록이 중심 화면이 되지 않게 (plan §20).
 */
export function SourceInbox({ only, title = "받은 자료함" }: { only?: string[]; title?: string } = {}) {
  const q = useQuery({
    queryKey: ["sources", "inbox"],
    queryFn: () => api.sources({ attention: true, limit: 50 }),
    refetchInterval: (query) => pending(query.state.data),
  });
  // c0052: 정리(경험 추출)는 서버가 알아서 한다. 여기서는 누를 단추 없이 상태만, 다시 뽑기는 자료 화면에서
  const rows = (q.data ?? []).filter((s) => !only || (s.attention && only.includes(s.attention)));
  if (!rows.length && !q.isError) return null;
  return (
    <section aria-labelledby="inbox-title">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="inbox-title" className="text-[15px] font-semibold">
          {title} <span className="font-normal text-muted tabular-nums">{rows.length}</span>
        </h2>
        <Link
          to="/brain/sources"
          className="inline-flex min-h-9 shrink-0 items-center text-[13px] text-muted hover:text-fg sm:min-h-7"
        >
          모두 보기 <ChevronRight size={14} />
        </Link>
      </div>
      <ErrorText error={q.error} className="mt-2" />
      <ul className="mt-3 divide-y divide-line border-y border-line">
        {rows.map((src) => {
          if (src.attention === "purpose_unconfirmed") return <ConfirmRow key={src.id} src={src} />;
          const why = src.attention ? ATTENTION_LABEL[src.attention] : null;
          return (
            <li key={src.id}>
              <Link
                to="/brain/sources/$sourceId"
                params={{ sourceId: src.id }}
                className={cn(ROW, "flex min-w-0 items-center gap-4")}
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{src.title}</div>
                  <div className="line-clamp-2 text-[13px] text-muted tabular-nums">{sourceMeta(src)}</div>
                </div>
                {why ? (
                  <span className={cn("shrink-0 text-[13px] tabular-nums", TONE_TEXT[why.tone])}>
                    {why.label}
                    {src.attention === "pending_claims" ? ` ${src.pending_claims}` : ""}
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * 넣을 때 무엇인지 확신하지 못한 자료 (c0039). 제안한 용도를 미리 골라 두고, 한 번 눌러 확인하거나 바꾼다.
 * 확인 전에는 이 기기 밖으로 보내지 않는다 (서버가 restricted로 둔다).
 */
function ConfirmRow({ src }: { src: SourceListItem }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (purpose: SourcePurpose) =>
      api.updateSourcePurpose(src.id, { purpose, data_class: purpose === "proof" ? "restricted" : "private" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      void qc.invalidateQueries({ queryKey: ["source", src.id] });
      void qc.invalidateQueries({ queryKey: ["experiences"] });
      void qc.invalidateQueries({ queryKey: ["credentials"] });
    },
  });
  const why = reasonText(src.purpose_hint, src.purpose);
  return (
    <li className="space-y-2 py-3">
      <div className="flex items-start gap-4">
        <Link
          to="/brain/sources/$sourceId"
          params={{ sourceId: src.id }}
          className="-mx-2 min-w-0 flex-1 rounded-md px-2 py-0.5 transition-colors duration-150 hover:bg-subtle/60"
        >
          <div className="truncate text-sm font-medium">{src.title}</div>
          <div className="truncate text-[13px] text-muted tabular-nums">{sourceMeta(src, false)}</div>
        </Link>
        <span className="shrink-0 pt-0.5 text-[13px] font-semibold">확인 필요</span>
      </div>
      {why ? <p className="text-[13px] text-muted">{why}. 맞으면 확인을 누르세요.</p> : null}
      <div className="-ml-1 flex flex-wrap items-center gap-1">
        <FilterChips<SourcePurpose>
          label={`${src.title} 용도`}
          value={src.purpose}
          onChange={(v) => save.mutate(v)}
          items={PURPOSE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        />
        <Button
          size="sm"
          variant="primary"
          className="ml-1"
          disabled={save.isPending}
          onClick={() => save.mutate(src.purpose)}
        >
          확인
        </Button>
      </div>
      <ErrorText error={save.error} />
    </li>
  );
}

// ---------------------------------------------------------------- 받은 자료 (받은 자료함의 전체 목록)

const sourcesRoute = getRouteApi("/brain/sources");

export function SourcesPage() {
  const { purpose } = sourcesRoute.useSearch();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["sources", "all", purpose ?? null],
    queryFn: () => api.sources({ purpose, limit: 500 }),
    refetchInterval: (query) => pending(query.state.data),
  });
  const setPurpose = (p: SourcePurpose | undefined) =>
    navigate({ to: "/brain/sources", search: p ? { purpose: p } : {}, replace: true });
  return (
    <div>
      <PageHeader
        title="받은 자료"
        back={{ to: "/brain", label: "브레인" }}
        subtitle="브레인에 넣은 파일과 붙여넣은 글입니다."
      />
      <FilterChips<SourcePurpose | undefined>
        label="용도 필터"
        value={purpose}
        onChange={setPurpose}
        items={[{ value: undefined, label: "전체" }, ...PURPOSE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))]}
        className="mb-4"
      />
      <Loaded query={q}>
        {(rows) =>
          rows.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {rows.map((src) => (
                <li key={src.id}>
                  <Link
                    to="/brain/sources/$sourceId"
                    params={{ sourceId: src.id }}
                    className={cn(ROW, "flex items-center gap-4")}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{src.title}</div>
                      <div className="line-clamp-2 text-[13px] text-muted tabular-nums">{sourceMeta(src, !purpose)}</div>
                    </div>
                    <SourceState src={src} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="border-t border-line pt-3 text-sm text-muted">
              {purpose ? `${PURPOSE_LABEL[purpose]}가 없습니다.` : "아직 받은 자료가 없습니다."}{" "}
              {purpose ? (
                <button type="button" onClick={() => setPurpose(undefined)} className={TEXT_LINK}>
                  필터 초기화
                </button>
              ) : (
                "브레인에서 자료 넣기로 시작합니다."
              )}
            </p>
          )
        }
      </Loaded>
    </div>
  );
}

/** 목록 오른쪽 상태 한 줄: 손이 가야 하면 그 이유, 아니면 회색으로 처리 결과. */
function SourceState({ src }: { src: SourceListItem }) {
  const why = src.attention ? ATTENTION_LABEL[src.attention] : null;
  if (why)
    return (
      <span className={cn("shrink-0 text-[13px] tabular-nums", TONE_TEXT[why.tone])}>
        {why.label}
        {src.attention === "pending_claims" ? ` ${src.pending_claims}` : ""}
      </span>
    );
  const done =
    src.purpose === "experience"
      ? src.claims_created
        ? `사실 ${src.claims_created}`
        : "추출함"
      : src.pages_total
        ? `${src.pages_total}쪽`
        : "보관함";
  return <span className="shrink-0 text-[13px] text-muted tabular-nums">{done}</span>;
}

// ---------------------------------------------------------------- 자료 하나

const sourceRoute = getRouteApi("/brain/sources/$sourceId");

export function SourcePage() {
  const { sourceId } = sourceRoute.useParams();
  const q = useQuery({
    queryKey: ["source", sourceId],
    queryFn: () => api.source(sourceId),
    refetchInterval: (query) => {
      const d = query.state.data;
      const parse = d?.versions.at(-1)?.parse;
      const busy = !parse || ["pending", "waiting_provider"].includes(parse.status) || d?.extraction?.status === "pending";
      return busy ? 3000 : false;
    },
  });
  return (
    <Loaded query={q}>
      {(src) => {
        const v = src.versions.at(-1);
        const parse = [...src.versions].reverse().find((x) => x.parse)?.parse ?? null;
        return (
          <div>
            <PageHeader
              title={src.title}
              back={{ to: "/brain", label: "브레인" }}
              subtitle={
                <span className="tabular-nums">
                  {[
                    v?.original_filename !== src.title ? v?.original_filename : null,
                    v ? fileSize(v.size_bytes) : null,
                    parse?.pages_total ? `${parse.pages_total}쪽` : null,
                    src.created_at ? formatDate(src.created_at) : null,
                    DATA_CLASS_LABEL[src.data_class] ?? src.data_class,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              }
            />
            <div className="max-w-2xl space-y-10">
              <PurposeSection src={src} parseDone={!!parse && ["complete", "partial"].includes(parse.status)} />
              {src.purpose === "proof" ? <ProofPanel sourceId={src.id} /> : null}
              <Block title="처리">
                <dl className="divide-y divide-line border-y border-line text-[13px]">
                  {src.origin_url ? (
                    <Row name="가져온 곳">
                      <a
                        href={src.origin_url}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        className="break-all underline-offset-2 hover:underline"
                      >
                        {hostOf(src.origin_url)}
                      </a>
                      <span className="text-muted"> · 내 브라우저에서 보냄</span>
                    </Row>
                  ) : null}
                  <Row name="읽기">
                    {parse ? (
                      <span className={TONE_TEXT[PARSE_STATUS[parse.status]?.tone ?? "none"]}>
                        {PARSE_STATUS[parse.status]?.label ?? parse.status}
                        {/* "읽음 · 이 기기에서 읽음"처럼 겹치지 않게: 어디서 읽었는지만 붙인다 */}
                        {parse.read_by === "device" ? " · 이 기기에서" : ""}
                        {parse.read_by === "model" ? " · 연결된 모델로" : ""}
                        {parse.missing_pages.length ? ` · 못 읽은 쪽 ${parse.missing_pages.join(", ")}` : ""}
                        {parse.error_code ? ` · ${parseFailNote(parse.error_code, v?.mime_type)}` : ""}
                      </span>
                    ) : (
                      <span className="text-muted">차례를 기다리는 중. 오래 걸리면 연결·관리 › 작업에서 상태를 보세요.</span>
                    )}
                  </Row>
                  {src.purpose === "experience" ? (
                    <Row name="경험 추출">
                      <ExtractionState src={src} parseRunId={parse?.run_id ?? null} parseDone={!!parse && ["complete", "partial"].includes(parse.status)} />
                    </Row>
                  ) : (
                    <Row name="경험 추출">
                      <span className="text-muted">하지 않음 — {PURPOSE_LABEL[src.purpose]}</span>
                    </Row>
                  )}
                  {src.pending_claims ? (
                    <Row name="확인할 사실">
                      <Link to="/brain/experiences" search={{ review: true }} className={cn(TEXT_LINK, "font-semibold tabular-nums")}>
                        {src.pending_claims}개 확인하기
                      </Link>
                      {src.pending_claims > 1 ? (
                        <ConfirmAllButton sourceId={src.id} count={src.pending_claims} />
                      ) : null}
                    </Row>
                  ) : null}
                  {src.confirmed_claims ? (
                    <Row name="확인한 사실">
                      <span className="tabular-nums">{src.confirmed_claims}개가 이 자료를 근거로 씁니다</span>
                    </Row>
                  ) : null}
                </dl>
              </Block>
              {src.purpose === "experience" &&
              parse &&
              ["complete", "partial"].includes(parse.status) &&
              looksLikeCoverLetter(src.title, v?.original_filename) ? (
                <CoverLetterAnswers parseRunId={parse.run_id} sourceTitle={src.title} />
              ) : null}
              {parse && ["complete", "partial"].includes(parse.status) ? <BlocksSection parseRunId={parse.run_id} /> : null}
            </div>
          </div>
        );
      }}
    </Loaded>
  );
}

function ExtractionState({
  src,
  parseRunId,
  parseDone,
}: {
  src: { id: string; extraction: { status: string; error_code: string | null; claims_created: number } | null };
  parseRunId: string | null;
  parseDone: boolean;
}) {
  const qc = useQueryClient();
  const extract = useMutation({
    mutationFn: () => api.requestExtraction(parseRunId!),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["source", src.id] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
  const ext = src.extraction;
  const canRequest = parseDone && parseRunId && (!ext || ext.status === "failed");
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {ext ? (
        <span className={TONE_TEXT[EXTRACTION_STATUS[ext.status]?.tone ?? "none"]}>
          {EXTRACTION_STATUS[ext.status]?.label ?? ext.status}
          {ext.status !== "pending" && ext.status !== "failed" ? ` · 사실 ${ext.claims_created}` : ""}
          {ext.error_code ? ` · ${ERROR_HINT[ext.error_code] ?? ext.error_code}` : ""}
        </span>
      ) : (
        <span className="text-muted">{parseDone ? "아직 요청하지 않음" : "읽기가 끝나면 요청할 수 있습니다"}</span>
      )}
      {canRequest ? (
        <Button size="sm" className="max-sm:h-9" onClick={() => extract.mutate()} disabled={extract.isPending}>
          {ext ? "다시 추출" : "경험 추출"}
        </Button>
      ) : null}
      <ErrorText error={extract.error} className="basis-full" />
    </div>
  );
}

const ERROR_HINT: Record<string, string> = {
  purpose_changed: "용도를 바꿔 멈춤",
  source_not_experience: "내 경험 자료가 아님",
  egress_denied: "이 기기 밖으로 보내지 않는 자료",
};

function PurposeSection({
  src,
  parseDone,
}: {
  src: {
    id: string;
    purpose: SourcePurpose;
    data_class: string;
    pending_claims: number;
    confirmed_claims: number;
    purpose_pending?: boolean;
    purpose_hint?: string | null;
  };
  parseDone: boolean;
}) {
  const qc = useQueryClient();
  const [purpose, setPurpose] = useState<SourcePurpose>(src.purpose);
  const [dataClass, setDataClass] = useState(src.data_class);
  useEffect(() => {
    setPurpose(src.purpose);
    setDataClass(src.data_class);
  }, [src.purpose, src.data_class]);
  const save = useMutation({
    mutationFn: () =>
      api.updateSourcePurpose(src.id, {
        purpose,
        data_class: purpose === "proof" ? "restricted" : (dataClass as "public" | "private" | "restricted"),
      }),
    onSuccess: (r) => {
      if (r.changed)
        toast(`${PURPOSE_LABEL[r.purpose]}로 저장했습니다.`, {
          description:
            [
              r.discarded_claims ? `확인 전 사실 ${r.discarded_claims}개를 닫았습니다.` : "",
              r.extraction_run_id ? "경험 추출을 요청했습니다." : "",
            ]
              .filter(Boolean)
              .join(" ") || undefined,
        });
      void qc.invalidateQueries({ queryKey: ["source", src.id] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
      void qc.invalidateQueries({ queryKey: ["experiences"] });
    },
  });
  // c0039: 넣을 때 확신하지 못한 용도를 그대로 확인 (확인 대기 동안의 제한 등급은 고른 용도의 기본으로)
  const confirm = useMutation({
    mutationFn: () =>
      api.updateSourcePurpose(src.id, {
        purpose: src.purpose,
        data_class: src.purpose === "proof" ? "restricted" : "private",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["source", src.id] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
      void qc.invalidateQueries({ queryKey: ["experiences"] });
    },
  });
  const changed = purpose !== src.purpose || (purpose !== "proof" && dataClass !== src.data_class);
  const blocked = changed && purpose !== "experience" && src.purpose === "experience" && src.confirmed_claims > 0;

  let consequence: string | null = null;
  if (blocked)
    consequence = `확인한 사실 ${src.confirmed_claims}개가 이 자료를 근거로 써서 용도를 바꿀 수 없습니다. 그 사실을 먼저 보관하세요.`;
  else if (purpose !== src.purpose && src.purpose === "experience" && src.pending_claims)
    consequence = `저장하면 이 자료에서 뽑은 확인 전 사실 ${src.pending_claims}개를 닫습니다.`;
  else if (purpose === "experience" && src.purpose !== "experience")
    consequence = parseDone
      ? dataClass === "restricted"
        ? "저장하면 경험 추출을 요청합니다. 이 기기 밖으로 보내지 않는 자료라 이 기기의 모델이 없으면 추출하지 못합니다."
        : "저장하면 경험 추출을 요청합니다. 모델 비용이 듭니다."
      : "저장하면 읽기가 끝난 뒤 경험 추출을 요청할 수 있습니다.";

  return (
    <Block title="용도">
      {src.purpose_pending && !changed ? (
        <div className="space-y-2">
          <p className="text-[13px]">
            <span className="font-semibold">확인 필요.</span>{" "}
            {reasonText(src.purpose_hint, src.purpose) ?? "넣을 때 무엇인지 확신하지 못했습니다"}. 확인 전에는 이 기기 밖으로
            보내지 않습니다.
          </p>
          <Button variant="primary" disabled={confirm.isPending} onClick={() => confirm.mutate()}>
            이대로 확인
          </Button>
          <ErrorText error={confirm.error} />
        </div>
      ) : null}
      <PurposeChoice
        name="src-purpose"
        hideLegend
        value={purpose}
        onChange={(v) => {
          setPurpose(v);
          save.reset();
        }}
      />
      {purpose !== "proof" ? (
        <div className="max-w-sm">
          <Label htmlFor="src-class">보내는 범위</Label>
          <Select id="src-class" value={dataClass} onValueChange={(v) => setDataClass(v)}>
            <SelectItem value="private">내 연결에만 보냄</SelectItem>
            <SelectItem value="restricted">이 기기 밖으로 보내지 않음</SelectItem>
            <SelectItem value="public">공개 자료</SelectItem>
          </Select>
        </div>
      ) : null}
      {changed ? (
        <div className="space-y-2">
          {consequence ? (
            <p className={cn("text-[13px]", blocked ? "text-danger" : "text-muted")}>{consequence}</p>
          ) : null}
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => save.mutate()} disabled={blocked || save.isPending}>
              저장
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setPurpose(src.purpose);
                setDataClass(src.data_class);
                save.reset();
              }}
            >
              취소
            </Button>
          </div>
        </div>
      ) : null}
      <ErrorText error={save.error} />
    </Block>
  );
}

function BlocksSection({ parseRunId }: { parseRunId: string }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["blocks", parseRunId],
    queryFn: () => api.blocks(parseRunId),
    enabled: open,
    staleTime: Infinity,
  });
  return (
    <Disclosure title="읽은 글 보기" open={open} onOpenChange={setOpen}>
      {open ? (
        <div className="mt-3">
          <Loaded query={q}>
            {(blocks) =>
              blocks.length ? (
                <div className="space-y-4 border-t border-line pt-3">
                  {blocks.map((b) => (
                    <div key={b.id}>
                      <p className="mb-1 text-xs text-muted tabular-nums">
                        블록 {b.ordinal + 1}
                        {b.page ? ` · ${b.page}쪽` : ""}
                      </p>
                      <p className="text-sm leading-7 break-words whitespace-pre-wrap">{b.text}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="border-t border-line pt-3 text-sm text-muted">읽은 글이 없습니다.</p>
              )
            }
          </Loaded>
        </div>
      ) : null}
    </Disclosure>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-[15px] font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 py-2.5 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-4">
      <dt className="font-medium">{name}</dt>
      <dd>{children}</dd>
    </div>
  );
}
