// 브레인에 넣기 (c0039): 넣은 것 하나하나를 읽고 → 무엇인지 고르고 → 보낼 곳으로 보낸다.
//
// - 고르기는 이 서버의 규칙으로만 한다 (POST /core/ingest/classify, 모델 없음). 넣은 것은 증빙일 수 있다.
// - 사진: 증빙이거나 무엇인지 모르면 이 기기에서 읽는다(lib/ocr.ts). 증빙이 아닌 것이 확실하고 연결된 모델이
//   사진을 읽을 수 있으면 서버가 모델로 읽는다.
// - 확신하지 못하면 "확인 필요"로 받은 자료함에 둔다 (서버가 restricted로 보관). 사람이 한 번 누르면 간다.
// - 주소: 공고는 공고 저장 경로로, 회사 페이지는 회사 조사 경로로.
//
// 큐는 화면 밖에 둔다 (창을 닫아도 계속 처리하고, 다시 열면 그대로 보인다).

import type {
  IngestCapabilities,
  IngestGuess,
  IngestKind,
  Source,
  SourcePurpose,
  TextLayerIn,
} from "@career-brain/client";
import { useSyncExternalStore } from "react";
import { api, ApiError, queryClient } from "./api";
import { maskResidentNumbers, recognizeImage, textLayerOf } from "./ocr";

export type IngestStage = "waiting" | "reading" | "saving" | "splitting" | "saved" | "confirm" | "failed";
export type ReadPath = "device" | "model" | "server";

export type IngestInput =
  | { type: "file"; file: File }
  | { type: "text"; text: string; title: string }
  | { type: "url"; url: string };

export interface IngestItem {
  id: string;
  input: IngestInput;
  name: string;
  /** 지금 고른 것 (제안 또는 사람이 고른 것) */
  kind: IngestKind | null;
  suggested: IngestKind | null;
  confidence: "high" | "low" | null;
  reason: string | null;
  /** 사람이 골랐다 */
  picked: boolean;
  stage: IngestStage;
  /** 이 기기에서 읽는 중의 진행 (0–1) */
  progress: number | null;
  readBy: ReadPath | null;
  /** 한 줄 설명 (저장됨 뒤의 결과, 실패 이유) */
  note: string | null;
  sourceId?: string;
  postingId?: string;
  companyId?: string;
  companyName?: string;
  companyHint?: string | null;
  host?: string | null;
  residentNumber?: boolean;
}

export const FILE_KINDS: SourcePurpose[] = ["experience", "proof", "reference"];
export const URL_KINDS: IngestKind[] = ["posting", "company"];
export const KIND_LABEL: Record<IngestKind, string> = {
  experience: "내 경험 자료",
  proof: "증빙 서류",
  reference: "참고 자료",
  posting: "공고",
  company: "회사 페이지",
};

export const ACCEPT =
  ".pdf,.docx,.pptx,.xlsx,.hwpx,.hwp,.png,.jpg,.jpeg,.txt,.md,.html,application/pdf," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
  "application/vnd.openxmlformats-officedocument.presentationml.presentation," +
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet," +
  "application/hwp+zip,application/x-hwp,image/png,image/jpeg,text/plain,text/markdown,text/html";
const TEXT_LIKE = /\.(txt|md|markdown|html?)$/i;
const IMAGE = /^image\/(png|jpe?g)$/i;
const HEAD_CHARS = 2000;
const MAX_PASTE = 200_000;
const PARSE_DONE = new Set(["complete", "partial", "failed"]);
const EXTRACT_DONE = new Set(["complete", "partial", "failed"]);
const POLL_MS = 2000;
const POLL_LIMIT_MS = 10 * 60_000;
const PARALLEL = 3;
const EXTRACT_KEY = "career-brain:ingest-extract";

// ---------------------------------------------------------------- 상태

let items: IngestItem[] = [];
const listeners = new Set<() => void>();
let seq = 0;

function emit() {
  items = [...items];
  listeners.forEach((l) => l());
}

function patch(id: string, p: Partial<IngestItem>) {
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) return;
  items[i] = { ...items[i]!, ...p };
  emit();
}

const get = (id: string) => items.find((x) => x.id === id);

export function useIngestQueue(): IngestItem[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => items,
    () => items,
  );
}

let extractPref: boolean = (() => {
  try {
    return localStorage.getItem(EXTRACT_KEY) !== "off";
  } catch {
    return true;
  }
})();
const prefListeners = new Set<() => void>();

/** 내 경험 자료는 읽은 뒤 바로 경험 후보를 뽑는다 (모델 비용). 이 브라우저에만 기억한다 */
export function useAutoExtract(): [boolean, (v: boolean) => void] {
  const v = useSyncExternalStore(
    (cb) => {
      prefListeners.add(cb);
      return () => prefListeners.delete(cb);
    },
    () => extractPref,
    () => extractPref,
  );
  return [
    v,
    (next) => {
      extractPref = next;
      try {
        localStorage.setItem(EXTRACT_KEY, next ? "on" : "off");
      } catch {
        // 저장소를 못 쓰면 이 탭에서만
      }
      prefListeners.forEach((l) => l());
    },
  ];
}

// ---------------------------------------------------------------- 넣기

function make(input: IngestInput, name: string, kind: IngestKind | null): IngestItem {
  seq += 1;
  return {
    id: `i${Date.now().toString(36)}${seq}`,
    input,
    name,
    kind,
    suggested: null,
    confidence: null,
    reason: null,
    picked: kind !== null,
    stage: "waiting",
    progress: null,
    readBy: null,
    note: null,
  };
}

/** ``kind``: 화면이 정한 용도 (증빙 서류 화면에서 열면 proof). 없으면 고른다 */
export function addFiles(files: Iterable<File>, kind?: SourcePurpose) {
  const added = [...files].map((f) => make({ type: "file", file: f }, f.name || "붙여넣은 그림", kind ?? null));
  if (!added.length) return;
  items = [...added, ...items];
  emit();
  added.forEach((it) => enqueue(it.id));
}

const URL_LINE = /^https?:\/\/\S+$/i;

/** 붙여넣은 글. 모든 줄이 주소면 주소마다 하나씩, 아니면 글 하나 */
export function addPaste(raw: string, kind?: SourcePurpose) {
  const text = raw.slice(0, MAX_PASTE).trim();
  if (!text) return;
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  let added: IngestItem[];
  if (lines.every((l) => URL_LINE.test(l))) {
    added = [...new Set(lines)].map((u) => make({ type: "url", url: u }, u, null));
  } else {
    const title = (lines[0] ?? "붙여넣은 글").replace(/\s+/g, " ").slice(0, 60);
    added = [make({ type: "text", text, title }, title, kind ?? null)];
  }
  items = [...added, ...items];
  emit();
  added.forEach((it) => enqueue(it.id));
}

export function removeItem(id: string) {
  items = items.filter((x) => x.id !== id);
  emit();
}

export function clearFinished() {
  items = items.filter((x) => x.stage !== "saved");
  emit();
}

// ---------------------------------------------------------------- 차례

let running = 0;
const pending: string[] = [];

function enqueue(id: string) {
  pending.push(id);
  pump();
}

function pump() {
  while (running < PARALLEL && pending.length) {
    const id = pending.shift()!;
    running += 1;
    void process(id)
      .catch((err) => patch(id, { stage: "failed", progress: null, note: failNote(err) }))
      .finally(() => {
        running -= 1;
        pump();
      });
  }
}

let capsPromise: Promise<IngestCapabilities> | null = null;
function caps(): Promise<IngestCapabilities> {
  capsPromise ??= api.ingestCapabilities().catch(() => ({ model_ocr_classes: [], model_ocr_route: null }));
  return capsPromise;
}

async function classify(body: { filename?: string; mime?: string; text_head?: string; url?: string }) {
  const [g] = await api.classifyIngest([{ key: "1", ...body }]);
  return g as IngestGuess;
}

function adopt(id: string, g: IngestGuess) {
  const it = get(id);
  if (!it) return;
  patch(id, {
    suggested: g.kind,
    confidence: g.confidence,
    reason: g.reason,
    kind: it.picked ? it.kind : g.kind,
    residentNumber: g.resident_number || it.residentNumber,
    companyHint: g.company_hint,
    companyId: g.company_id ?? undefined,
    companyName: g.company_name ?? undefined,
    host: g.host,
  });
}

const sure = (it: IngestItem) => it.picked || it.confidence === "high";

async function process(id: string) {
  const it = get(id);
  if (!it) return;
  if (it.input.type === "url") return processUrl(id, it.input.url);
  if (it.input.type === "text") {
    patch(id, { stage: "reading" });
    adopt(id, await classify({ mime: "text/plain", text_head: it.input.text.slice(0, HEAD_CHARS) }));
    const file = new File([it.input.text], `${safeName(it.input.title)}.txt`, { type: "text/plain" });
    return upload(id, file, it.input.title);
  }
  return processFile(id, it.input.file);
}

function safeName(title: string) {
  return (title.trim() || "붙여넣은 글").replace(/[\\/:*?"<>|]+/g, " ").slice(0, 80);
}

async function processFile(id: string, file: File) {
  patch(id, { stage: "reading" });
  const mime = file.type || undefined;
  let head: string | undefined;
  if (TEXT_LIKE.test(file.name)) head = (await file.slice(0, HEAD_CHARS * 4).text()).slice(0, HEAD_CHARS);
  adopt(id, await classify({ filename: file.name || undefined, mime, text_head: head }));
  let layer: TextLayerIn | undefined;
  if (IMAGE.test(file.type)) {
    const now = get(id)!;
    const c = await caps();
    // 증빙이 아닌 것이 확실하고 연결된 모델이 사진을 읽을 수 있으면 서버가 모델로 읽는다
    const modelOk = sure(now) && now.kind !== "proof" && c.model_ocr_classes.includes("private");
    if (!modelOk) {
      const r = await recognizeImage(file, (p) => patch(id, { progress: p }));
      patch(id, { progress: null, readBy: "device" });
      if (r.lines.length) {
        layer = textLayerOf(r);
        const g = await classify({ filename: file.name || undefined, mime, text_head: r.text.slice(0, HEAD_CHARS) });
        // 가린 주민등록번호가 있으면 증빙 (서버로 보낸 앞부분 글에는 이미 가려져 있다)
        if (r.masked) Object.assign(g, { kind: "proof", confidence: "high", reason: "resident_number", resident_number: true });
        adopt(id, g);
      }
    } else {
      patch(id, { readBy: "model" });
    }
  }
  return upload(id, file, undefined, layer);
}

async function upload(id: string, file: File, title?: string, layer?: TextLayerIn) {
  const it = get(id);
  if (!it) return;
  const purpose = (it.kind && FILE_KINDS.includes(it.kind as SourcePurpose) ? it.kind : "experience") as SourcePurpose;
  const waiting = !sure(it);
  patch(id, { stage: "saving" });
  const up = await api.uploadSource(file, {
    title,
    purpose,
    // 증빙과 확인 전 자료는 서버가 restricted로 둔다
    dataClass: purpose === "proof" || waiting ? "restricted" : "private",
    purposePending: waiting,
    purposeHint: waiting ? (it.reason ?? undefined) : undefined,
    textLayer: layer,
  });
  void queryClient.invalidateQueries({ queryKey: ["sources"] });
  patch(id, {
    sourceId: up.source_id,
    kind: up.deduplicated ? up.purpose : it.kind,
    note: up.deduplicated ? "이미 넣은 파일이라 그 자료를 씁니다" : null,
  });
  await afterParse(id, up.source_id, up.version_id);
}

async function pollSource(sourceId: string, versionId?: string): Promise<Source> {
  const started = Date.now();
  for (;;) {
    const src = await api.source(sourceId);
    const v = versionId ? src.versions.find((x) => x.id === versionId) : src.versions.at(-1);
    if (v?.parse && PARSE_DONE.has(v.parse.status)) return src;
    if (Date.now() - started > POLL_LIMIT_MS) throw new Error("slow");
    await sleep(POLL_MS);
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function afterParse(id: string, sourceId: string, versionId?: string) {
  patch(id, { stage: "reading" });
  let src = await pollSource(sourceId, versionId);
  const v = (versionId ? src.versions.find((x) => x.id === versionId) : src.versions.at(-1)) ?? src.versions.at(-1);
  const parse = v?.parse ?? null;
  const it = get(id);
  if (!it) return;
  if (parse?.read_by === "device" || parse?.read_by === "model") patch(id, { readBy: parse.read_by });
  else if (parse?.read_by) patch(id, { readBy: it.readBy ?? "server" });

  // PDF·DOCX·PPTX·XLSX·HWPX는 서버가 읽은 글로 한 번 더 고른다 (이름으로 확신하지 못했을 때만)
  if (src.purpose_pending && !it.picked && parse && parse.status !== "failed") {
    const blocks = await api.blocks(parse.run_id).catch(() => []);
    const head = maskResidentNumbers(blocks.map((b) => b.text).join("\n").slice(0, HEAD_CHARS));
    if (head.text.trim()) {
      const g = await classify({ filename: v?.original_filename, mime: v?.mime_type, text_head: head.text });
      if (head.masked) Object.assign(g, { kind: "proof", confidence: "high", reason: "resident_number" });
      adopt(id, g);
      const now = get(id)!;
      if (now.confidence === "high" && now.kind && FILE_KINDS.includes(now.kind as SourcePurpose)) {
        await route(id, now.kind as SourcePurpose);
        return;
      }
    }
  }
  src = await api.source(sourceId);
  if (src.purpose_pending) {
    patch(id, { stage: "confirm", kind: get(id)?.kind ?? src.purpose });
    return;
  }
  if (parse?.status === "failed" && !src.purpose_pending) {
    patch(id, { stage: "failed", note: parseFailNote(parse.error_code, v?.mime_type) });
    return;
  }
  await finish(id, src);
}

/** 용도를 정해 보낸다 (확인 필요 → 사람이 누름, 또는 저장 뒤 바꾸기) */
async function route(id: string, purpose: SourcePurpose) {
  const it = get(id);
  if (!it?.sourceId) return;
  patch(id, { stage: "saving", kind: purpose, picked: true });
  await api.updateSourcePurpose(it.sourceId, {
    purpose,
    data_class: purpose === "proof" ? "restricted" : "private",
  });
  void queryClient.invalidateQueries({ queryKey: ["sources"] });
  void queryClient.invalidateQueries({ queryKey: ["source", it.sourceId] });
  const src = await api.source(it.sourceId);
  const parse = src.versions.at(-1)?.parse;
  if (parse?.status === "failed") {
    patch(id, { stage: "failed", note: parseFailNote(parse.error_code, src.versions.at(-1)?.mime_type) });
    return;
  }
  await finish(id, src);
}

async function finish(id: string, src: Source) {
  const it = get(id);
  if (!it) return;
  const parse = src.versions.at(-1)?.parse;
  if (src.purpose === "experience") {
    let ext = src.extraction;
    if (!ext && extractPref && parse && ["complete", "partial"].includes(parse.status)) {
      try {
        const r = await api.requestExtraction(parse.run_id);
        ext = { id: r.id, status: "pending", error_code: null, claims_created: 0 };
      } catch (err) {
        patch(id, { stage: "saved", note: `경험 후보는 아직 뽑지 않았습니다 · ${failNote(err)}` });
        return;
      }
    }
    if (ext) {
      patch(id, { stage: "splitting" });
      const started = Date.now();
      let run = await api.extraction(ext.id);
      while (!EXTRACT_DONE.has(run.status) && Date.now() - started < POLL_LIMIT_MS) {
        await sleep(POLL_MS * 1.5);
        run = await api.extraction(ext.id);
      }
      void queryClient.invalidateQueries({ queryKey: ["experiences"] });
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
      if (run.status === "failed") {
        // 자료는 저장됐다. 뽑기만 못 했으니 "실패"로 보이지 않는다
        patch(id, { stage: "saved", note: `${extractFailNote(run.error_code)} · 자료 화면에서 다시 뽑을 수 있습니다` });
        return;
      }
      if (!EXTRACT_DONE.has(run.status)) {
        patch(id, { stage: "splitting", note: "오래 걸리면 연결·관리 › 작업에서 상태를 보세요" });
        return;
      }
      patch(id, {
        stage: "saved",
        note: `경험 ${run.experiences_created}개 · 확인할 사실 ${run.claims_created}개`,
      });
      return;
    }
    patch(id, { stage: "saved", note: "경험 후보는 자료 화면에서 뽑을 수 있습니다" });
    return;
  }
  if (src.purpose === "proof") {
    void queryClient.invalidateQueries({ queryKey: ["credentials"] });
    void queryClient.invalidateQueries({ queryKey: ["proofs"] });
    patch(id, { stage: "saved", note: "원본을 보관하고 자격·이수 항목을 뽑았습니다" });
    return;
  }
  patch(id, { stage: "saved", note: "참고 자료로 보관했습니다" });
}

// ---------------------------------------------------------------- 주소

async function processUrl(id: string, url: string) {
  patch(id, { stage: "reading" });
  adopt(id, await classify({ url }));
  const it = get(id)!;
  if (!sure(it) || (it.kind === "company" && !it.companyId)) {
    patch(id, { stage: "confirm" });
    return;
  }
  await sendUrl(id);
}

async function sendUrl(id: string, companyName?: string) {
  const it = get(id);
  if (!it || it.input.type !== "url") return;
  const url = it.input.url;
  patch(id, { stage: "saving" });
  if (it.kind === "posting") {
    const [r] = await api.savePostings([url]);
    void queryClient.invalidateQueries({ queryKey: ["postings"] });
    if (!r || r.error || !r.posting_id) {
      patch(id, { stage: "failed", note: r?.error === "invalid_url" ? "주소 형식이 맞지 않습니다" : "공고를 저장하지 못했습니다" });
      return;
    }
    patch(id, {
      stage: "saved",
      postingId: r.posting_id,
      note: r.deduplicated ? "이미 저장한 공고입니다" : "공고를 가져오는 중입니다",
    });
    return;
  }
  let companyId = it.companyId;
  let name = it.companyName;
  if (!companyId) {
    name = (companyName ?? "").trim();
    if (!name) {
      patch(id, { stage: "confirm" });
      return;
    }
    const origin = new URL(url).origin;
    const made = await api.createCompany({ name, homepage: origin });
    companyId = made.id;
  }
  await api.importCompanyUrl(companyId, url);
  void queryClient.invalidateQueries({ queryKey: ["companies"] });
  patch(id, { stage: "saved", companyId, companyName: name, note: `${name ?? "회사"}에 페이지를 가져오는 중입니다` });
}

// ---------------------------------------------------------------- 사람이 고름

/** 한 번 눌러 고르기. 저장 전이면 그 용도로 보내고, 저장 뒤면 용도를 바꾼다 */
export function choose(id: string, kind: IngestKind, companyName?: string) {
  const it = get(id);
  if (!it) return;
  const run = async () => {
    if (it.input.type === "url") {
      if (it.stage === "saved" || it.stage === "saving") return;
      patch(id, { kind, picked: true });
      if (kind === "company" && !get(id)!.companyId && !companyName?.trim()) {
        patch(id, { stage: "confirm" });
        return;
      }
      await sendUrl(id, companyName);
      return;
    }
    if (!FILE_KINDS.includes(kind as SourcePurpose)) return;
    if (it.sourceId) {
      await route(id, kind as SourcePurpose);
      return;
    }
    // 아직 올리기 전 (읽는 중): 고른 것으로 올린다
    patch(id, { kind, picked: true });
  };
  void run().catch((err) => patch(id, { stage: "failed", note: failNote(err) }));
}

// ---------------------------------------------------------------- 말

function failNote(err: unknown): string {
  if (err instanceof ApiError) {
    const code = err.code ?? "";
    const known: Record<string, string> = {
      unsupported_type: "PDF·DOCX·PPTX·XLSX·한글·사진·텍스트만 넣을 수 있습니다",
      mime_mismatch: "파일 내용과 확장자가 맞지 않습니다",
      empty: "빈 파일입니다",
      too_large: "파일이 너무 큽니다",
      source_has_confirmed_claims: "확인한 사실이 이 자료를 근거로 써서 바꿀 수 없습니다",
      source_has_reference_items: "참고 글이 이 자료를 써서 바꿀 수 없습니다",
      no_model: "경험 후보를 뽑을 모델 연결이 없습니다",
      forbidden: "이 토큰으로는 넣을 수 없습니다",
    };
    if (known[code]) return known[code]!;
    if (err.status === 403) return "이 토큰으로는 넣을 수 없습니다";
    if (err.status === 413) return "파일이 너무 큽니다";
    return "서버가 받지 못했습니다";
  }
  if (err instanceof Error && err.message === "slow") return "오래 걸리면 연결·관리 › 작업에서 상태를 보세요";
  return "이 기기에서 읽지 못했습니다";
}

export function parseFailNote(code: string | null | undefined, mime?: string): string {
  if (mime === "application/pdf" && (code === "ocr_unavailable" || code === "no_text" || code === "egress_denied"))
    return "글자가 없는 PDF입니다. 쪽을 사진으로 넣으면 이 기기에서 읽습니다";
  const known: Record<string, string> = {
    ocr_unavailable: "글자를 찾지 못했습니다",
    egress_denied: "글자를 찾지 못했습니다",
    no_text: "글자를 찾지 못했습니다",
    encrypted: "암호가 걸린 파일입니다",
    malformed: "파일이 깨져 읽지 못했습니다",
    too_many_pages: "쪽이 너무 많습니다 (300쪽까지)",
    too_many_blocks: "글이 너무 많아 읽지 못했습니다",
    file_too_large: "파일 안의 내용이 너무 커서 읽지 못했습니다",
    file_too_complex: "파일 구성이 너무 복잡해 읽지 못했습니다",
    hwp_unsupported: "한글 97 형식(.hwp)은 아직 읽지 못합니다. 한글에서 HWPX나 PDF로 저장해 넣어 주세요.",
    unsupported_type: "이 형식은 읽지 못합니다",
  };
  if (code?.startsWith("model_ocr_denied")) return "연결된 모델이 이 자료를 받지 않습니다";
  if (code?.startsWith("model_ocr_failed")) return "연결된 모델이 읽지 못했습니다";
  return (code && known[code]) || "읽지 못했습니다";
}

function extractFailNote(code: string | null): string {
  const known: Record<string, string> = {
    no_model: "경험 후보를 뽑을 모델 연결이 없습니다",
    no_route: "경험 후보를 뽑을 모델 연결이 없습니다",
    api_key_missing: "모델 연결의 키가 없습니다",
    egress_denied: "이 등급은 연결된 모델로 보내지 않습니다",
    budget_exceeded: "이번 달 예산을 넘었습니다",
  };
  return (code && known[code]) || "경험 후보를 뽑지 못했습니다";
}

/** 받은 자료함·창에서 쓰는 제안 이유 한 줄 */
export function reasonText(reason: string | null | undefined, kind: IngestKind | null | undefined): string | null {
  if (!reason || !kind) return null;
  const label = KIND_LABEL[kind];
  const why: Record<string, string> = {
    resident_number: "주민등록번호가 보여",
    image: "글자가 없는 사진이라",
    first_person: "내가 한 일을 적은 글이라",
    default: "",
    job_host: "채용 사이트 주소라",
    job_host_listing: "채용 사이트 주소라",
    posting_path: "공고 주소 모양이라",
    posting_query: "공고 주소 모양이라",
    company_path: "회사 소개 주소라",
    careers_landing: "채용 첫 화면이라",
    home: "회사 첫 화면이라",
  };
  const w = reason in why ? why[reason] : `“${reason}” 낱말이 있어`;
  return w ? `${w} ${label}로 골랐습니다` : `${label}로 골랐습니다`;
}

