// 이 기기(브라우저)에서 사진의 글자를 읽는다 (c0039, plan §9). 증빙은 이 길로만 읽는다.
//
// - tesseract.js(Apache-2.0)를 쓰고, 엔진(wasm)과 한국어·영어 학습 데이터는 이 앱이 직접 내준다
//   (`/ocr/…`, vite.config.ts의 ocrAssets). 실행 중에 CDN에서 받지 않는다.
// - 처음 사진을 읽을 때만 불러온다 (기본 화면 묶음에 들어가지 않는다). 학습 데이터는 이 브라우저의
//   IndexedDB에 캐시된다.
// - 결과는 줄 단위 글과 그 줄의 위치(px, 원본 그림 기준). 위치가 없는 줄은 위치 없이 보낸다.
// - 보내기 전에 주민등록번호 모양을 가린다 (서버도 한 번 더 가린다). 원본 그림은 그대로 올린다.

import type { TextLayerBlock, TextLayerIn } from "@career-brain/client";
import { tidyOcrLines } from "./ocrTidy";

const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/ocr`;
/** 큰 사진은 이 길이(긴 변, px)로 줄여 읽는다. 위치는 원본 크기로 되돌린다 */
const MAX_EDGE = 2400;
const IDLE_MS = 60_000;
export const OCR_ENGINE = "tesseract.js 7 (kor+eng, 이 기기)";

// 서버(domain.sensitive._RRN)와 같은 규칙
const RRN =
  /(?<![\d-])(\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))\s?-?\s?([1-8])(\d{6})(?![\d-])/g;
export const RESIDENT_MASK = "******-*******";

export function maskResidentNumbers(text: string): { text: string; masked: number } {
  let masked = 0;
  const out = text.replace(RRN, () => {
    masked += 1;
    return RESIDENT_MASK;
  });
  return { text: out, masked };
}

export interface OcrResult {
  lines: TextLayerBlock[];
  text: string;
  /** 가린 주민등록번호 수 */
  masked: number;
  width: number;
  height: number;
}

type TesseractWorker = Awaited<ReturnType<(typeof import("tesseract.js"))["createWorker"]>>;

let workerPromise: Promise<TesseractWorker> | null = null;
let idleTimer: ReturnType<typeof setTimeout> | null = null;
let progressListener: ((p: number) => void) | null = null;

/** WebAssembly SIMD를 쓸 수 있으면 빠른 엔진, 아니면 기본 엔진 (둘 다 이 앱이 내준다) */
function corePath() {
  const simd = WebAssembly.validate(
    new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]),
  );
  const file = simd ? "tesseract-core-simd-lstm.wasm.js" : "tesseract-core-lstm.wasm.js";
  return new URL(`${BASE}/core/${file}`, location.href).href;
}

async function worker(): Promise<TesseractWorker> {
  if (!workerPromise) {
    workerPromise = (async () => {
      const mod = await import("tesseract.js/dist/tesseract.esm.min.js");
      const Tesseract = mod.default;
      return Tesseract.createWorker(["kor", "eng"], 1 /* OEM.LSTM_ONLY */, {
        workerPath: new URL(`${BASE}/worker.min.js`, location.href).href,
        corePath: corePath(),
        langPath: new URL(`${BASE}/lang`, location.href).href,
        gzip: true,
        logger: (m) => {
          if (m.status === "recognizing text") progressListener?.(m.progress);
        },
      });
    })();
    workerPromise.catch(() => {
      workerPromise = null;
    });
  }
  return workerPromise;
}

function scheduleIdle() {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    const w = workerPromise;
    workerPromise = null;
    void w?.then((x) => x.terminate()).catch(() => undefined);
  }, IDLE_MS);
}

async function prepare(file: Blob): Promise<{ image: HTMLCanvasElement | Blob; scale: number; width: number; height: number }> {
  const bitmap = await createImageBitmap(file);
  const { width, height } = bitmap;
  const long = Math.max(width, height);
  if (long <= MAX_EDGE) {
    bitmap.close();
    return { image: file, scale: 1, width, height };
  }
  const scale = MAX_EDGE / long;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return { image: canvas, scale, width, height };
}

/** 한 번에 하나씩 읽는다 (worker 하나) */
let chain: Promise<unknown> = Promise.resolve();

export function recognizeImage(file: Blob, onProgress?: (p: number) => void): Promise<OcrResult> {
  const run = chain.then(async () => {
    const w = await worker();
    if (idleTimer) clearTimeout(idleTimer);
    progressListener = onProgress ?? null;
    try {
      const { image, scale, width, height } = await prepare(file);
      const { data } = await w.recognize(image, {}, { blocks: true, text: true });
      const lines: TextLayerBlock[] = [];
      let masked = 0;
      for (const block of data.blocks ?? []) {
        for (const para of block.paragraphs) {
          for (const line of para.lines) {
            const raw = line.text.replace(/\s+/g, " ").trim();
            if (!raw) continue;
            const m = maskResidentNumbers(raw);
            masked += m.masked;
            const b = line.bbox;
            const box =
              b && b.x1 >= b.x0 && b.y1 >= b.y0
                ? ([b.x0, b.y0, b.x1, b.y1].map((v) => Math.max(0, Math.round(v / scale))) as [
                    number,
                    number,
                    number,
                    number,
                  ])
                : null;
            lines.push({ page: 1, text: m.text, bbox: box });
          }
        }
      }
      const tidy = tidyOcrLines(lines);
      return { lines: tidy, text: tidy.map((l) => l.text).join("\n"), masked, width, height };
    } finally {
      progressListener = null;
      scheduleIdle();
    }
  });
  chain = run.catch(() => undefined);
  return run;
}

export function textLayerOf(r: OcrResult): TextLayerIn {
  return { engine: OCR_ENGINE, pages_total: 1, blocks: r.lines, masked: r.masked };
}
