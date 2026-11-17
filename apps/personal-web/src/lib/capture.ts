// 보고 있는 페이지 보내기 (c0043, plan §8): 북마크·휴대폰 공유로 받은 페이지를 다루는 순수 함수들.
//
// 북마크는 다른 사이트 위에서 돈다. 그 페이지에는 토큰·쿠키를 두지 않는다: 북마크는 이 앱의 `/capture`를
// 새 창으로 열고, 그 창이 "준비됨"을 알린 뒤에 페이지 내용을 이 앱 주소로만(postMessage targetOrigin) 보낸다.
// API는 다른 출처에 열지 않는다(CORS 없음). 저장은 이 앱 창이 자기 토큰으로 한다.
//
// 받은 HTML은 믿지 않는다: 화면에 그리지 않고, DOMParser로 만든 문서(스크립트가 돌지 않고 그림을 받지 않는다)
// 에서 본문만 골라 Markdown으로 바꾼다. 미리 보기는 글자로만 보여 준다.

export const CAPTURE_MESSAGE = "career-brain:capture";
export const READY_MESSAGE = "career-brain:ready";
/** 북마크가 보내는 HTML 상한 (서버 `MAX_CAPTURE_HTML`과 같다) */
export const MAX_CAPTURE_HTML = 2_000_000;
/** 선택한 글·공유한 글 상한 (서버 `MAX_MANUAL_CHARS`와 같다) */
export const MAX_CAPTURE_TEXT = 60_000;
/** Markdown 자료 상한 (서버 `MAX_CAPTURE_TEXT`) */
export const MAX_CAPTURE_MARKDOWN = 300_000;
const MAX_URL = 2000;
const MAX_TITLE = 300;

/** 받은 페이지 한 건 */
export interface CapturedPage {
  url: string;
  title: string;
  /** 사용자가 선택한 글 (없으면 빈 값) */
  selection: string;
  /** 페이지 HTML (휴대폰 공유는 없음) */
  html: string;
  /** 휴대폰에서 공유한 글 */
  text: string;
  via: "bookmarklet" | "share";
}

/**
 * 북마크 주소. `origin`은 이 앱의 주소 (예: https://brain.example). 사이트 쪽에서 하는 일은 셋뿐이다:
 * 창 열기 → 그 창이 이 앱 주소에서 "준비됨"을 보내면 → 페이지 주소·제목·선택한 글·HTML을 그 주소로만 한 번 보낸다.
 */
export function bookmarkletHref(origin: string): string {
  const code = `(()=>{const O=${JSON.stringify(origin)};const w=window.open(O+"/capture","career_brain_capture","popup,width=560,height=780");if(!w){alert("팝업이 막혔습니다. 이 사이트에서 팝업을 허용한 뒤 다시 눌러 주세요.");return}let h="";try{h=document.documentElement.outerHTML.slice(0,${MAX_CAPTURE_HTML})}catch(e){}const d={type:${JSON.stringify(CAPTURE_MESSAGE)},v:1,url:location.href,title:String(document.title||"").slice(0,${MAX_TITLE}),selection:String(getSelection()||"").slice(0,${MAX_CAPTURE_TEXT}),html:h};let sent=false;const on=e=>{if(sent||e.source!==w||e.origin!==O||!e.data||e.data.type!==${JSON.stringify(READY_MESSAGE)})return;sent=true;removeEventListener("message",on);w.postMessage(d,O)};addEventListener("message",on);setTimeout(()=>removeEventListener("message",on),600000)})();void 0`;
  return `javascript:${encodeURIComponent(code)}`;
}

function httpUrl(raw: unknown): URL | null {
  if (typeof raw !== "string" || !raw || raw.length > MAX_URL) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

function str(v: unknown, max: number): string | null {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v.slice(0, max) : null;
}

/**
 * 북마크가 보낸 메시지를 확인한다. 모양이 다르면 null (받지 않는다).
 * 페이지 주소의 출처가 보낸 창의 출처와 같아야 한다 — 다른 사이트인 척하는 주소는 받지 않는다.
 */
export function parseCaptureMessage(data: unknown, origin: string): CapturedPage | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.type !== CAPTURE_MESSAGE || d.v !== 1) return null;
  const url = httpUrl(d.url);
  if (!url || url.origin !== origin) return null;
  const title = str(d.title, MAX_TITLE);
  const selection = str(d.selection, MAX_CAPTURE_TEXT);
  const html = str(d.html, MAX_CAPTURE_HTML);
  if (title === null || selection === null || html === null) return null;
  if (!html.trim() && !selection.trim()) return null;
  return { url: url.href, title: title.trim(), selection: selection.trim(), html, text: "", via: "bookmarklet" };
}

const URL_IN_TEXT = /https?:\/\/[^\s<>"]+/;

/**
 * 휴대폰 공유(Web Share Target, `/capture?title=&text=&url=`). 많은 앱이 주소를 `text`에 넣는다.
 * 주소 말고 남은 글이 없거나 제목과 같거나 한 줄로 짧으면 "주소 공유"(공고로 저장하면 서버가 가져온다),
 * 아니면 "글 공유"(주소는 원래 주소로만 둔다).
 */
export function parseShared(search: URLSearchParams): CapturedPage | null {
  const title = (search.get("title") ?? "").trim().slice(0, MAX_TITLE);
  const text = (search.get("text") ?? "").trim().slice(0, MAX_CAPTURE_TEXT);
  let url = httpUrl((search.get("url") ?? "").trim())?.href ?? "";
  if (!url) url = httpUrl(text.match(URL_IN_TEXT)?.[0])?.href ?? "";
  if (!title && !text && !url) return null;
  const rest = url ? text.replace(url, "").replace(URL_IN_TEXT, "").trim() : text;
  const short = rest.length <= 120 && !rest.includes("\n");
  const linkOnly = !!url && (!rest || rest === title || short);
  return {
    url,
    title: title || (linkOnly ? rest.replace(/\s+/g, " ") : ""),
    selection: "",
    html: "",
    text: linkOnly ? "" : text,
    via: "share",
  };
}

/** 이 주소의 쿼리에 공유한 것이 있었나 (있으면 주소창에서 곧바로 지운다) */
export const SHARE_KEYS = ["title", "text", "url"];

export interface Extracted {
  title: string;
  /** 본문 Markdown (그림은 설명 글만, 링크는 남긴다) */
  markdown: string;
  /** 미리 보기용 글 (Markdown 기호를 걷은 것) */
  plain: string;
}

/** Defuddle이 바깥 API를 부르려 하면 막는다 (`useAsync: false`와 함께 두 겹) */
const refuseFetch = (() => Promise.reject(new Error("no network during capture"))) as typeof fetch;

/**
 * 받은 HTML에서 본문을 골라 Markdown으로. DOMParser 문서는 화면에 붙지 않아 스크립트·그림·스타일을
 * 받지 않는다. 본문 고르기는 Defuddle(MIT), Markdown은 Turndown(MIT) — 둘 다 앱에 묶어 낸다.
 */
export async function extractPage(html: string, url: string, fallbackTitle = ""): Promise<Extracted> {
  const [defuddleMod, turndownMod] = await Promise.all([import("defuddle"), import("turndown")]);
  const doc = new DOMParser().parseFromString(html, "text/html");
  // 상대 주소 링크를 원래 페이지 기준으로 (inert 문서 안에서만)
  const base = doc.createElement("base");
  base.setAttribute("href", url);
  doc.head?.prepend(base);
  let title = fallbackTitle;
  let content = "";
  try {
    const mod = defuddleMod as unknown as { default: unknown };
    const Defuddle = ((mod.default as { default?: unknown }).default ?? mod.default) as new (
      d: Document,
      o: Record<string, unknown>,
    ) => { parse(): { title?: string; content?: string } };
    const r = new Defuddle(doc, { url, useAsync: false, fetch: refuseFetch }).parse();
    title = (r.title || fallbackTitle).trim();
    content = r.content ?? "";
  } catch {
    content = "";
  }
  if (!content.trim()) content = doc.body?.innerHTML ?? "";
  const TurndownService = turndownMod.default;
  const td = new TurndownService({ headingStyle: "atx", codeBlockStyle: "fenced", bulletListMarker: "-" });
  td.remove(["script", "style", "noscript", "iframe", "object", "embed", "form", "button", "svg"]);
  // 그림 주소는 남기지 않는다 (나중에 글을 열 때 그 사이트로 요청이 가지 않게). 설명 글만
  td.addRule("imageAlt", {
    filter: "img",
    replacement: (_content, node) => {
      const alt = ((node as Element).getAttribute("alt") ?? "").trim();
      return alt ? `[그림: ${alt}]` : "";
    },
  });
  const markdown = tidy(td.turndown(content)).slice(0, MAX_CAPTURE_MARKDOWN);
  return { title: title.slice(0, MAX_TITLE), markdown, plain: plainOf(markdown) };
}

function tidy(md: string): string {
  return md
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 미리 보기 글: Markdown 기호를 걷는다 (그리지 않고 글자로만 보여 준다) */
export function plainOf(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^[#>\-*+\s]+/gm, "")
    .replace(/[*_`]/g, "")
    .replace(/\n{2,}/g, "\n")
    .trim();
}
