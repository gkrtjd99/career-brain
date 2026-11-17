import type { CapturePostingOut, UploadOut } from "@career-brain/client";
import { Button, cn, Input, Label, Spinner } from "@career-brain/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ErrorText } from "../components/common";
import { api } from "../lib/api";
import {
  extractPage,
  parseCaptureMessage,
  parseShared,
  plainOf,
  READY_MESSAGE,
  SHARE_KEYS,
  type CapturedPage,
  type Extracted,
} from "../lib/capture";
import { hostOf } from "../lib/format";
import { PURPOSE_OPTIONS } from "../lib/labels";

type Target = "posting" | "reference" | "experience";

const PURPOSE_TEXT = Object.fromEntries(PURPOSE_OPTIONS.map((o) => [o.value, o])) as Record<
  string,
  (typeof PURPOSE_OPTIONS)[number]
>;

/** 휴대폰 공유로 열렸으면 그 값 (화면이 곧바로 주소창에서 지운다: 기록·새로고침에 남지 않게) */
function readShared(): CapturedPage | null {
  const params = new URLSearchParams(window.location.search);
  if (!SHARE_KEYS.some((k) => params.has(k))) return null;
  return parseShared(params);
}

/**
 * 보고 있는 페이지 보내기 (c0043, plan §8). 북마크가 연 창이거나 휴대폰 공유로 연 화면.
 * 받은 것은 믿지 않는다: HTML은 그리지 않고 글만 고른다. 저장을 누르기 전에는 아무것도 넣지 않는다.
 * 창 하나에 한 건만 받는다.
 */
export function CapturePage() {
  const navigate = useNavigate();
  const [shared] = useState(readShared);
  const [page, setPage] = useState<CapturedPage | null>(shared);
  const hasOpener = useRef(!shared && !!window.opener);
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    if (window.location.search) void navigate({ to: "/capture", replace: true });
  }, [navigate]);

  // 북마크와 약속: 이 창이 준비됨을 알린 뒤, 연 창이 보낸 메시지 하나만 받는다
  useEffect(() => {
    if (!hasOpener.current) return;
    const opener = window.opener as Window | null;
    if (!opener) return;
    let announced = false;
    let taken = false;
    const on = (e: MessageEvent) => {
      if (!announced || taken || e.source !== opener) return;
      const got = parseCaptureMessage(e.data, e.origin);
      if (!got) return;
      taken = true;
      window.removeEventListener("message", on);
      try {
        // 받은 뒤에는 연 창과의 연결을 끊는다
        window.opener = null;
      } catch {
        // 바꿀 수 없으면 그대로 둔다 (더 받지 않는다)
      }
      setPage(got);
    };
    window.addEventListener("message", on);
    // 준비됨에는 아무 내용도 싣지 않는다. 연 창의 출처를 모르므로 "*"
    opener.postMessage({ type: READY_MESSAGE, v: 1 }, "*");
    announced = true;
    const timer = window.setTimeout(() => setSlow(true), 5000);
    return () => {
      window.removeEventListener("message", on);
      window.clearTimeout(timer);
    };
  }, []);

  return (
    <div className="mx-auto max-w-lg pt-6 pb-10 md:pt-10">
      <h1 className="text-xl leading-8 font-semibold">브레인으로 보내기</h1>
      {page ? (
        <Preview key={page.url + page.title} page={page} />
      ) : hasOpener.current ? (
        <div className="mt-6 space-y-3">
          <p role="status" className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> 보고 있는 페이지를 받는 중입니다.
          </p>
          {slow ? (
            <p className="border-t border-line pt-3 text-[13px] leading-6 text-muted">
              오래 걸리면 이 창을 닫고 페이지에서 북마크를 다시 눌러 주세요. 일부 사이트는 브라우저가 이 연결을
              막습니다. 그럴 때는 본문을 복사해 브레인에 넣기에 붙여넣습니다.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="mt-6 space-y-2 border-t border-line pt-4 text-sm leading-6 text-muted">
          <p>이 화면은 '브레인으로 보내기' 북마크나 휴대폰 공유로 엽니다.</p>
          <p>
            북마크는{" "}
            <Link to="/manage" search={{ tab: "settings" }} className="text-fg underline underline-offset-4">
              연결·관리 › 설정
            </Link>
            에서 받습니다.
          </p>
        </div>
      )}
    </div>
  );
}

interface Body {
  /** 본문을 고르는 중 */
  reading: boolean;
  extracted: Extracted | null;
}

function useBody(page: CapturedPage): Body {
  const q = useQuery({
    queryKey: ["capture-extract", page.url, page.html.length],
    queryFn: () => extractPage(page.html, page.url, page.title),
    enabled: !!page.html,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  });
  if (!page.html) {
    const text = page.text;
    return { reading: false, extracted: text ? { title: page.title, markdown: text, plain: plainOf(text) } : null };
  }
  return { reading: q.isPending, extracted: q.data ?? null };
}

function Preview({ page }: { page: CapturedPage }) {
  const qc = useQueryClient();
  const body = useBody(page);
  const linkOnly = page.via === "share" && !page.text && !!page.url;
  const [title, setTitle] = useState(page.title);
  const [onlySelection, setOnlySelection] = useState(!!page.selection);
  const [target, setTarget] = useState<Target | null>(null);

  // 주소로 공고인지 짐작한다 (모델 없이 서버 규칙). 사람이 고르면 그것이 이긴다
  const guess = useQuery({
    queryKey: ["capture-classify", page.url],
    queryFn: async () => (await api.classifyIngest([{ key: "1", url: page.url }]))[0] ?? null,
    enabled: !!page.url && !linkOnly,
    staleTime: Infinity,
    retry: false,
  });
  useEffect(() => {
    if (!title && body.extracted?.title) setTitle(body.extracted.title);
  }, [body.extracted?.title, title]);

  const selected = onlySelection && page.selection ? page.selection : "";
  const markdown = selected || body.extracted?.markdown || "";
  const plain = selected || body.extracted?.plain || "";
  const canPosting = !!page.url && (linkOnly || !!page.html || !!page.text);
  const canSource = !linkOnly && !!markdown.trim();
  const fallback: Target =
    canPosting && (linkOnly || (guess.data?.kind === "posting" && guess.data.confidence === "high"))
      ? "posting"
      : canSource
        ? "reference"
        : "posting";
  const chosen: Target = target ?? fallback;

  const save = useMutation({
    mutationFn: async (): Promise<SaveResult> => {
      const name = title.trim() || hostOf(page.url) || "보낸 글";
      if (chosen === "posting") {
        if (linkOnly) {
          const [r] = await api.savePostings([page.url]);
          if (!r?.posting_id) throw new Error(r?.error ?? "invalid_url");
          return { kind: "posting", out: null, postingId: r.posting_id, fetching: !r.deduplicated };
        }
        const out = await api.capturePosting({
          url: page.url,
          title: name,
          html: page.html || undefined,
          text: selected || page.text || undefined,
        });
        return { kind: "posting", out, postingId: out.posting_id, fetching: false };
      }
      if (page.url) {
        const out = await api.captureSource({ url: page.url, title: name, markdown, purpose: chosen });
        return { kind: "source", out };
      }
      // 주소 없이 공유한 글: 브레인에 넣기와 같은 길 (붙여넣은 글)
      const file = new File([markdown], `${name.replace(/[\\/:*?"<>|]+/g, " ").slice(0, 80)}.md`, {
        type: "text/markdown",
      });
      const out = await api.uploadSource(file, { title: name, purpose: chosen, dataClass: "private" });
      return { kind: "source", out };
    },
    onSuccess: (r) => {
      if (r.kind === "posting") void qc.invalidateQueries({ queryKey: ["postings"] });
      else void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });

  const options: { value: Target; label: string; text: string; disabled: boolean }[] = [
    {
      value: "posting",
      label: "공고",
      text: linkOnly
        ? "이 주소의 페이지를 서버가 가져와 공고로 둡니다."
        : "공고 항목을 뽑아 공고 화면에 둡니다. 확인한 뒤 저장 공고가 됩니다.",
      disabled: !canPosting,
    },
    { value: "reference", ...pick("reference"), disabled: !canSource },
    { value: "experience", ...pick("experience"), disabled: !canSource },
  ];
  const host = page.url ? hostOf(page.url) : null;
  const length = (selected || (page.html ? body.extracted?.plain : page.text) || "").length;
  const newWindow = page.via === "bookmarklet";

  if (save.isSuccess)
    return <Saved result={save.data} newWindow={newWindow} purpose={chosen} />;

  return (
    <div className="mt-5 space-y-6">
      <div className="space-y-3">
        <div>
          <Label htmlFor="capture-title">제목</Label>
          <Input id="capture-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
        </div>
        <p className="flex flex-wrap gap-x-1.5 text-[13px] text-muted">
          {host ? <span className="min-w-0 truncate text-fg">{host}</span> : <span>주소 없음</span>}
          {length ? <span className="tabular-nums">· {length.toLocaleString()}자</span> : null}
          {page.via === "share" ? <span>· 공유로 받음</span> : null}
        </p>
        {body.reading ? (
          <p role="status" className="flex items-center gap-2 text-[13px] text-muted">
            <Spinner /> 본문을 고르는 중입니다.
          </p>
        ) : plain ? (
          // 받은 글은 그리지 않고 글자로만 보여 준다
          <p className="line-clamp-6 border-l border-line pl-3 text-[13px] leading-6 whitespace-pre-line text-muted">
            {plain.slice(0, 1200)}
          </p>
        ) : linkOnly ? null : (
          <p className="text-[13px] text-muted">본문을 찾지 못했습니다. 공고라면 서버가 구조화 데이터로 다시 봅니다.</p>
        )}
        {page.selection ? (
          <label className="flex min-h-9 cursor-pointer items-start gap-2.5 py-1 text-[13px]">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 accent-fg"
              checked={onlySelection}
              onChange={(e) => setOnlySelection(e.target.checked)}
            />
            <span>
              선택한 부분만 보내기
              <span className="block text-muted tabular-nums">선택한 글 {page.selection.length.toLocaleString()}자</span>
            </span>
          </label>
        ) : null}
      </div>

      <fieldset>
        <legend className="mb-1.5 text-sm font-medium">넣을 곳</legend>
        <div className="divide-y divide-line border-y border-line">
          {options.map((o) => (
            <div key={o.value} className="py-0.5">
              <label
                className={cn(
                  "-mx-2 flex min-h-9 cursor-pointer items-start gap-3 rounded-md px-2 py-2.5 transition-colors duration-150",
                  "hover:bg-subtle/60 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
                  o.disabled && "cursor-default opacity-50 hover:bg-transparent",
                )}
              >
                <input
                  type="radio"
                  name="capture-target"
                  value={o.value}
                  checked={chosen === o.value}
                  disabled={o.disabled}
                  onChange={() => setTarget(o.value)}
                  className="mt-1 size-4 shrink-0 accent-fg focus-visible:outline-none"
                />
                <span className="min-w-0">
                  <span className={cn("text-sm", chosen === o.value ? "font-semibold" : "font-medium")}>{o.label}</span>
                  <span className="block text-[13px] text-muted">{o.text}</span>
                </span>
              </label>
            </div>
          ))}
        </div>
      </fieldset>

      <div className="space-y-2">
        <ErrorText error={save.error} />
        <Button
          variant="primary"
          className="w-full"
          disabled={save.isPending || body.reading || options.find((o) => o.value === chosen)?.disabled}
          onClick={() => save.mutate()}
        >
          {save.isPending ? <Spinner /> : null}
          {chosen === "posting" ? "공고로 저장" : "브레인에 넣기"}
        </Button>
        <p className="text-[13px] text-muted">
          누르기 전에는 아무것도 넣지 않습니다. 페이지의 스크립트와 그림은 열지 않고 글만 읽습니다.
        </p>
      </div>
    </div>
  );
}

function pick(purpose: "reference" | "experience") {
  const o = PURPOSE_TEXT[purpose]!;
  return { label: o.label, text: o.text };
}

type SaveResult =
  | { kind: "posting"; out: CapturePostingOut | null; postingId: string; fetching: boolean }
  | { kind: "source"; out: UploadOut };

function savedMessage(result: SaveResult, purpose: Target): string {
  if (result.kind === "source") {
    if (result.out.deduplicated) return "이미 넣은 글이라 그 자료를 씁니다.";
    return purpose === "experience"
      ? "내 경험 자료로 넣었습니다. 경험 후보는 자료 화면에서 뽑습니다."
      : "참고 자료로 넣었습니다. 내 경험으로 쓰지 않습니다.";
  }
  const out = result.out;
  if (!out)
    return result.fetching
      ? "공고 화면에 넣었습니다. 서버가 페이지를 가져오는 중입니다."
      : "이미 저장한 주소라 그 공고를 씁니다.";
  if (out.status === "listing") return "공고 목록 페이지라 공고 후보를 모았습니다. 공고 화면에서 고릅니다.";
  if (out.unchanged) return "같은 내용이 이미 있어 새 버전을 만들지 않았습니다.";
  return "공고 화면에 넣었습니다. 항목을 확인하면 저장 공고가 됩니다.";
}

const OPEN_LINK =
  "inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent/90";

function Saved({ result, newWindow, purpose }: { result: SaveResult; newWindow: boolean; purpose: Target }) {
  // 북마크 창은 작아서 저장한 것을 새 탭에서 연다
  const target = newWindow ? "_blank" : undefined;
  return (
    <div className="mt-6 space-y-4 border-t border-line pt-4">
      <p role="status" className="text-sm">
        {savedMessage(result, purpose)}
      </p>
      <div className="flex flex-wrap gap-2">
        {result.kind === "posting" ? (
          <Link to="/postings/$postingId" params={{ postingId: result.postingId }} target={target} className={OPEN_LINK}>
            공고 열기
          </Link>
        ) : (
          <Link
            to="/brain/sources/$sourceId"
            params={{ sourceId: result.out.source_id }}
            target={target}
            className={OPEN_LINK}
          >
            자료 열기
          </Link>
        )}
        {newWindow ? <Button onClick={() => window.close()}>창 닫기</Button> : null}
      </div>
    </div>
  );
}
